// ast_helper parses a single Go source file and emits a JSON summary of its
// HTTP handlers, gRPC registrations, struct types, imports, and log calls.
// Usage: ast_helper <path/to/file.go>
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
)

// ---- output types ----

type FileResult struct {
	Package            string              `json:"package"`
	Imports            []string            `json:"imports"`
	Functions          []FunctionInfo      `json:"functions"`
	HTTPHandlers       []HTTPHandler       `json:"http_handlers"`
	GRPCRegistrations  []GRPCRegistration  `json:"grpc_registrations"`
	StructTypes        []StructType        `json:"struct_types"`
	LogCalls           []LogCall           `json:"log_calls"`
	DBCalls            []DBCall            `json:"db_calls"`
}

type FunctionInfo struct {
	Name      string  `json:"name"`
	StartLine int     `json:"start_line"`
	EndLine   int     `json:"end_line"`
}

type HTTPHandler struct {
	Pattern          string `json:"pattern"`
	Method           string `json:"method"`
	HandlerFunc      string `json:"handler_func"`
	RegistrationLine int    `json:"registration_line"`
	RouterType       string `json:"router_type"`
}

type GRPCRegistration struct {
	ServiceName      string `json:"service_name"`
	RegistrationLine int    `json:"registration_line"`
}

type StructField struct {
	Name    string `json:"name"`
	Type    string `json:"type"`
	JSONTag string `json:"json_tag,omitempty"`
}

type StructType struct {
	Name   string        `json:"name"`
	Fields []StructField `json:"fields"`
}

type LogCall struct {
	FuncName string   `json:"func_name"`
	Args     []string `json:"args"`
	Line     int      `json:"line"`
}

type DBCall struct {
	FuncName string   `json:"func_name"`
	Args     []string `json:"args"`
	Line     int      `json:"line"`
}

// ---- main ----

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: ast_helper <file.go>")
		os.Exit(1)
	}
	filePath := os.Args[1]

	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filePath, nil, parser.ParseComments)
	if err != nil {
		fmt.Fprintf(os.Stderr, "parse error: %v\n", err)
		os.Exit(1)
	}

	result := extract(fset, f)
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(result); err != nil {
		fmt.Fprintf(os.Stderr, "json encode error: %v\n", err)
		os.Exit(1)
	}
}

// ---- extraction logic ----

func extract(fset *token.FileSet, f *ast.File) FileResult {
	result := FileResult{
		Package:           f.Name.Name,
		Imports:           []string{},
		Functions:         []FunctionInfo{},
		HTTPHandlers:      []HTTPHandler{},
		GRPCRegistrations: []GRPCRegistration{},
		StructTypes:       []StructType{},
		LogCalls:          []LogCall{},
		DBCalls:           []DBCall{},
	}

	// Imports
	for _, imp := range f.Imports {
		path := strings.Trim(imp.Path.Value, `"`)
		result.Imports = append(result.Imports, path)
	}

	// Walk top-level declarations
	for _, decl := range f.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			result.Functions = append(result.Functions, FunctionInfo{
				Name:      d.Name.Name,
				StartLine: fset.Position(d.Pos()).Line,
				EndLine:   fset.Position(d.End()).Line,
			})
		case *ast.GenDecl:
			for _, spec := range d.Specs {
				if ts, ok := spec.(*ast.TypeSpec); ok {
					if st, ok := ts.Type.(*ast.StructType); ok {
						result.StructTypes = append(result.StructTypes, extractStruct(ts.Name.Name, st))
					}
				}
			}
		}
	}

	// Walk all expressions for handler registrations, log calls, db calls
	ast.Inspect(f, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}

		line := fset.Position(call.Pos()).Line

		sel, isSel := call.Fun.(*ast.SelectorExpr)
		if !isSel {
			return true
		}

		funcName := sel.Sel.Name
		receiverName := exprString(sel.X)
		fullName := receiverName + "." + funcName

		// HTTP handler registration
		if h, ok := tryHTTPHandler(call, sel, funcName, receiverName, line); ok {
			result.HTTPHandlers = append(result.HTTPHandlers, h)
			return true
		}

		// gRPC registration: pb.RegisterXxxServer(srv, handler)
		if strings.HasPrefix(funcName, "Register") && strings.HasSuffix(funcName, "Server") {
			serviceName := strings.TrimPrefix(funcName, "Register")
			serviceName = strings.TrimSuffix(serviceName, "Server")
			result.GRPCRegistrations = append(result.GRPCRegistrations, GRPCRegistration{
				ServiceName:      serviceName,
				RegistrationLine: line,
			})
			return true
		}

		// Log calls
		if isLogReceiver(receiverName) && isLogMethod(funcName) {
			args := extractStringArgs(call.Args)
			result.LogCalls = append(result.LogCalls, LogCall{
				FuncName: fullName,
				Args:     args,
				Line:     line,
			})
			return true
		}

		// DB calls
		if isDBReceiver(receiverName) && isDBMethod(funcName) {
			args := extractStringArgs(call.Args)
			result.DBCalls = append(result.DBCalls, DBCall{
				FuncName: fullName,
				Args:     args,
				Line:     line,
			})
			return true
		}

		return true
	})

	return result
}

func tryHTTPHandler(call *ast.CallExpr, sel *ast.SelectorExpr, funcName, receiverName string, line int) (HTTPHandler, bool) {
	// stdlib: http.HandleFunc("/path", HandlerFunc)
	if receiverName == "http" && funcName == "HandleFunc" && len(call.Args) >= 2 {
		pattern := stringLiteral(call.Args[0])
		handler := exprString(call.Args[1])
		if pattern != "" {
			return HTTPHandler{Pattern: pattern, Method: "ANY", HandlerFunc: handler, RegistrationLine: line, RouterType: "stdlib"}, true
		}
	}

	// gorilla/mux or chi or generic: r.Get("/path", fn), r.Post, r.Put, r.Delete, r.Patch, r.Handle, r.HandleFunc
	httpMethods := map[string]string{
		"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE",
		"Patch": "PATCH", "Head": "HEAD", "Options": "OPTIONS",
		"Handle": "ANY", "HandleFunc": "ANY",
	}
	if method, ok := httpMethods[funcName]; ok && len(call.Args) >= 2 {
		pattern := stringLiteral(call.Args[0])
		if pattern != "" {
			handler := exprString(call.Args[1])
			routerType := "gorilla/mux"
			// chi uses the same pattern; gin uses router.GET/POST
			if strings.Contains(receiverName, "router") || strings.Contains(receiverName, "engine") {
				routerType = "gin"
			}
			return HTTPHandler{Pattern: pattern, Method: method, HandlerFunc: handler, RegistrationLine: line, RouterType: routerType}, true
		}
	}

	// gin-style: router.GET, router.POST etc. (uppercase method names)
	ginMethods := map[string]string{
		"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE",
		"PATCH": "PATCH", "HEAD": "HEAD", "OPTIONS": "OPTIONS", "Any": "ANY",
	}
	if method, ok := ginMethods[funcName]; ok && len(call.Args) >= 2 {
		pattern := stringLiteral(call.Args[0])
		if pattern != "" {
			handler := exprString(call.Args[1])
			return HTTPHandler{Pattern: pattern, Method: method, HandlerFunc: handler, RegistrationLine: line, RouterType: "gin"}, true
		}
	}

	return HTTPHandler{}, false
}

func isLogReceiver(name string) bool {
	lower := strings.ToLower(name)
	return lower == "slog" || lower == "log" || strings.Contains(lower, "logger") ||
		lower == "span" || strings.Contains(lower, "log")
}

func isLogMethod(name string) bool {
	return name == "Info" || name == "Warn" || name == "Warning" || name == "Error" ||
		name == "Debug" || name == "Fatal" || name == "With" || name == "AddEvent"
}

func isDBReceiver(name string) bool {
	lower := strings.ToLower(name)
	return lower == "db" || lower == "tx" || strings.Contains(lower, "db") ||
		strings.Contains(lower, "conn") || strings.Contains(lower, "pool")
}

func isDBMethod(name string) bool {
	return name == "Query" || name == "QueryRow" || name == "QueryContext" ||
		name == "QueryRowContext" || name == "Exec" || name == "ExecContext" ||
		name == "Get" || name == "Select" || name == "NamedQuery" || name == "NamedExec"
}

// ---- helpers ----

func extractStruct(name string, st *ast.StructType) StructType {
	s := StructType{Name: name}
	if st.Fields == nil {
		return s
	}
	for _, field := range st.Fields.List {
		typStr := typeString(field.Type)
		jsonTag := ""
		if field.Tag != nil {
			tag := strings.Trim(field.Tag.Value, "`")
			jsonTag = extractJSONTag(tag)
		}
		if len(field.Names) == 0 {
			// Embedded field
			s.Fields = append(s.Fields, StructField{Name: typStr, Type: typStr, JSONTag: jsonTag})
		}
		for _, name := range field.Names {
			s.Fields = append(s.Fields, StructField{Name: name.Name, Type: typStr, JSONTag: jsonTag})
		}
	}
	return s
}

func extractJSONTag(tag string) string {
	for _, part := range strings.Fields(tag) {
		if strings.HasPrefix(part, `json:"`) {
			val := strings.TrimPrefix(part, `json:"`)
			val = strings.TrimSuffix(val, `"`)
			name := strings.Split(val, ",")[0]
			if name != "-" {
				return name
			}
		}
	}
	return ""
}

func extractStringArgs(args []ast.Expr) []string {
	result := make([]string, 0, len(args))
	for _, arg := range args {
		result = append(result, exprString(arg))
	}
	return result
}

func stringLiteral(e ast.Expr) string {
	lit, ok := e.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return ""
	}
	return strings.Trim(lit.Value, `"`)
}

func exprString(e ast.Expr) string {
	if e == nil {
		return ""
	}
	switch v := e.(type) {
	case *ast.Ident:
		return v.Name
	case *ast.SelectorExpr:
		return exprString(v.X) + "." + v.Sel.Name
	case *ast.BasicLit:
		return v.Value
	case *ast.StarExpr:
		return "*" + exprString(v.X)
	case *ast.ArrayType:
		return "[]" + exprString(v.Elt)
	case *ast.MapType:
		return "map[" + exprString(v.Key) + "]" + exprString(v.Value)
	case *ast.CallExpr:
		return exprString(v.Fun) + "(...)"
	case *ast.IndexExpr:
		return exprString(v.X) + "[" + exprString(v.Index) + "]"
	case *ast.UnaryExpr:
		return v.Op.String() + exprString(v.X)
	default:
		return fmt.Sprintf("<%T>", e)
	}
}

func typeString(e ast.Expr) string {
	return exprString(e)
}
