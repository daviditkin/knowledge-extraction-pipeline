// ast_helper parses a single Go source file and emits a JSON summary of its
// HTTP handlers, gRPC registrations, struct types, imports, log calls, db calls,
// and per-function outbound call information for call-chain tracking.
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
	Package           string             `json:"package"`
	Imports           []string           `json:"imports"`
	Functions         []FunctionInfo     `json:"functions"`
	HTTPHandlers      []HTTPHandler      `json:"http_handlers"`
	GRPCRegistrations []GRPCRegistration `json:"grpc_registrations"`
	StructTypes       []StructType       `json:"struct_types"`
	LogCalls          []LogCall          `json:"log_calls"`
	DBCalls           []DBCall           `json:"db_calls"`
}

// CallEdge represents one intra-service call edge. StringArgs holds the
// string literals passed at the call site — used in the Python extractor
// to resolve URL arguments when the callee receives the URL as a function
// parameter (Pattern 2: url built at call site, passed into helper).
type CallEdge struct {
	Callee     string   `json:"callee"`
	StringArgs []string `json:"string_args"`
	Line       int      `json:"line"`
}

// FunctionInfo describes one function's outbound call behaviour.
// CallEdges contains every call edge for the intra-service call graph
// (plain "funcName" or "receiver.Method"), with the string literals
// passed at the call site. HTTPClientCalls and ClientLibCalls are the
// categorised subsets the Python extractor needs for call-chain resolution.
type FunctionInfo struct {
	Name            string           `json:"name"`
	StartLine       int              `json:"start_line"`
	EndLine         int              `json:"end_line"`
	CallEdges       []CallEdge       `json:"call_edges"`        // all call edges with call-site string args
	HTTPClientCalls []HTTPClientCall `json:"http_client_calls"` // direct net/http client calls
	ClientLibCalls  []ClientLibCall  `json:"client_lib_calls"`  // calls on *Client receivers
}

// HTTPClientCall is a direct outbound HTTP call (http.NewRequest, http.Get, …).
type HTTPClientCall struct {
	Func       string   `json:"func"`        // e.g. "http.NewRequest", "http.Get"
	MethodArg  string   `json:"method_arg"`  // "GET"/"POST" if first arg is a string literal
	StringArgs []string `json:"string_args"` // string literal args, with local vars resolved
	Line       int      `json:"line"`
}

// ClientLibCall is a call on a receiver whose name contains "client"
// (e.g. mcbsClient.StoreTemplate). These are resolved in a second pass
// once all services have been extracted.
type ClientLibCall struct {
	Receiver   string   `json:"receiver"`    // variable name, e.g. "mcbsClient"
	Method     string   `json:"method"`      // e.g. "StoreTemplate"
	StringArgs []string `json:"string_args"` // string literal args
	Line       int      `json:"line"`
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

	// Walk top-level declarations. Each FuncDecl is analysed in its own
	// scope so that all call information is attributed to the right function.
	for _, decl := range f.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			fi, httpHandlers, grpcRegs, logCalls, dbCalls := extractFunction(fset, d)
			result.Functions = append(result.Functions, fi)
			result.HTTPHandlers = append(result.HTTPHandlers, httpHandlers...)
			result.GRPCRegistrations = append(result.GRPCRegistrations, grpcRegs...)
			result.LogCalls = append(result.LogCalls, logCalls...)
			result.DBCalls = append(result.DBCalls, dbCalls...)

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

	return result
}

// extractFunction walks a single function body, returning categorised call
// information plus the "bubble-up" slices that belong on FileResult.
func extractFunction(fset *token.FileSet, d *ast.FuncDecl) (
	fi FunctionInfo,
	httpHandlers []HTTPHandler,
	grpcRegs []GRPCRegistration,
	logCalls []LogCall,
	dbCalls []DBCall,
) {
	fi = FunctionInfo{
		Name:            d.Name.Name,
		StartLine:       fset.Position(d.Pos()).Line,
		EndLine:         fset.Position(d.End()).Line,
		CallEdges:       []CallEdge{},
		HTTPClientCalls: []HTTPClientCall{},
		ClientLibCalls:  []ClientLibCall{},
	}
	httpHandlers = []HTTPHandler{}
	grpcRegs = []GRPCRegistration{}
	logCalls = []LogCall{}
	dbCalls = []DBCall{}

	if d.Body == nil {
		return
	}

	// Pre-pass: collect local variable → string value assignments so we can
	// resolve variable arguments in HTTP client calls (Pattern 1:
	// theurl := fmt.Sprintf("http://svc/path") … http.NewRequest("POST", theurl, …)).
	varMap := collectVarAssignments(d.Body)

	ast.Inspect(d.Body, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}

		line := fset.Position(call.Pos()).Line

		// Plain function call (no receiver): add to call graph with call-site strings.
		if ident, ok := call.Fun.(*ast.Ident); ok {
			fi.CallEdges = append(fi.CallEdges, CallEdge{
				Callee:     ident.Name,
				StringArgs: extractStringLiteralsResolved(call.Args, varMap),
				Line:       line,
			})
			return true
		}

		sel, isSel := call.Fun.(*ast.SelectorExpr)
		if !isSel {
			return true
		}

		funcName := sel.Sel.Name
		receiverName := exprString(sel.X)
		fullName := receiverName + "." + funcName

		// 1. HTTP handler registration (router setup)
		if h, ok := tryHTTPHandler(call, sel, funcName, receiverName, line); ok {
			httpHandlers = append(httpHandlers, h)
			return true
		}

		// 2. gRPC registration: pb.RegisterXxxServer(srv, handler)
		if strings.HasPrefix(funcName, "Register") && strings.HasSuffix(funcName, "Server") {
			serviceName := strings.TrimPrefix(funcName, "Register")
			serviceName = strings.TrimSuffix(serviceName, "Server")
			grpcRegs = append(grpcRegs, GRPCRegistration{
				ServiceName:      serviceName,
				RegistrationLine: line,
			})
			return true
		}

		// 3. Log calls
		if isLogReceiver(receiverName) && isLogMethod(funcName) {
			logCalls = append(logCalls, LogCall{
				FuncName: fullName,
				Args:     extractStringArgs(call.Args),
				Line:     line,
			})
			return true
		}

		// 4. DB calls
		if isDBReceiver(receiverName) && isDBMethod(funcName) {
			dbCalls = append(dbCalls, DBCall{
				FuncName: fullName,
				Args:     extractStringArgs(call.Args),
				Line:     line,
			})
			return true
		}

		// 5. Direct HTTP client calls (net/http package or http-named clients)
		if hc, ok := tryHTTPClientCall(call, funcName, receiverName, line, varMap); ok {
			fi.HTTPClientCalls = append(fi.HTTPClientCalls, hc)
			return true
		}

		// 6. Client library calls (receiver name contains "client", not already http)
		if isClientLibReceiver(receiverName) && !isHTTPClientReceiver(receiverName) {
			fi.ClientLibCalls = append(fi.ClientLibCalls, ClientLibCall{
				Receiver:   receiverName,
				Method:     funcName,
				StringArgs: extractStringLiteralsResolved(call.Args, varMap),
				Line:       line,
			})
			return true
		}

		// 7. Everything else → call graph edge with call-site string args
		fi.CallEdges = append(fi.CallEdges, CallEdge{
			Callee:     fullName,
			StringArgs: extractStringLiteralsResolved(call.Args, varMap),
			Line:       line,
		})
		return true
	})

	return
}

// tryHTTPClientCall detects outbound net/http client calls.
// varMap is used to resolve local variable arguments to their string values
// (Pattern 1: theurl := fmt.Sprintf("http://svc/path") … http.NewRequest(method, theurl, …)).
func tryHTTPClientCall(call *ast.CallExpr, funcName, receiverName string, line int, varMap map[string]string) (HTTPClientCall, bool) {
	// http.NewRequest(method, url, body)
	if receiverName == "http" && funcName == "NewRequest" {
		methodArg := ""
		if len(call.Args) >= 1 {
			methodArg = stringLiteralResolved(call.Args[0], varMap)
		}
		return HTTPClientCall{
			Func:       "http.NewRequest",
			MethodArg:  methodArg,
			StringArgs: extractStringLiteralsResolved(call.Args, varMap),
			Line:       line,
		}, true
	}

	// http.Get(url), http.Post(url, contentType, body), http.Put, http.Delete, http.Head
	shortHTTPMethods := map[string]string{
		"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Head": "HEAD",
	}
	if receiverName == "http" {
		if method, ok := shortHTTPMethods[funcName]; ok {
			return HTTPClientCall{
				Func:       "http." + funcName,
				MethodArg:  method,
				StringArgs: extractStringLiteralsResolved(call.Args, varMap),
				Line:       line,
			}, true
		}
	}

	// <httpClient>.Do(req) — receiver contains both "http" and "client", or is http.DefaultClient
	if funcName == "Do" && isHTTPClientReceiver(receiverName) {
		return HTTPClientCall{
			Func:       receiverName + ".Do",
			MethodArg:  "",
			StringArgs: extractStringLiteralsResolved(call.Args, varMap),
			Line:       line,
		}, true
	}

	return HTTPClientCall{}, false
}

// collectVarAssignments does a single-pass walk of a function body to build
// a map of varName → string value for simple string assignments:
//
//	x := "literal"
//	x := fmt.Sprintf("format", …)   — records the format string
//	x = "literal"
//
// Only direct string-literal or fmt.Sprintf-format assignments are tracked.
func collectVarAssignments(body *ast.BlockStmt) map[string]string {
	m := make(map[string]string)
	ast.Inspect(body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.AssignStmt:
			for i, lhs := range s.Lhs {
				ident, ok := lhs.(*ast.Ident)
				if !ok || i >= len(s.Rhs) {
					continue
				}
				if v := resolveStringExpr(s.Rhs[i]); v != "" {
					m[ident.Name] = v
				}
			}
		case *ast.DeclStmt:
			gd, ok := s.Decl.(*ast.GenDecl)
			if !ok {
				break
			}
			for _, spec := range gd.Specs {
				vs, ok := spec.(*ast.ValueSpec)
				if !ok {
					continue
				}
				for i, name := range vs.Names {
					if i >= len(vs.Values) {
						continue
					}
					if v := resolveStringExpr(vs.Values[i]); v != "" {
						m[name.Name] = v
					}
				}
			}
		}
		return true
	})
	return m
}

// resolveStringExpr returns the string value if the expression is a string
// literal or a fmt.Sprintf/fmt.Errorf call whose first argument is a string
// literal. Returns "" if the expression cannot be resolved.
func resolveStringExpr(e ast.Expr) string {
	switch v := e.(type) {
	case *ast.BasicLit:
		if v.Kind == token.STRING {
			return strings.Trim(v.Value, `"`)
		}
	case *ast.CallExpr:
		sel, ok := v.Fun.(*ast.SelectorExpr)
		if !ok {
			break
		}
		pkg, ok := sel.X.(*ast.Ident)
		if !ok {
			break
		}
		if pkg.Name == "fmt" && (sel.Sel.Name == "Sprintf" || sel.Sel.Name == "Errorf") {
			if len(v.Args) > 0 {
				return resolveStringExpr(v.Args[0])
			}
		}
	}
	return ""
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

	// gorilla/mux or chi: r.Get("/path", fn), r.Post, r.Put, r.Delete, r.Patch, r.Handle, r.HandleFunc
	// Exclude receiver == "http": http.Get/Post/etc. are client calls, not handler registrations.
	httpMethods := map[string]string{
		"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE",
		"Patch": "PATCH", "Head": "HEAD", "Options": "OPTIONS",
		"Handle": "ANY", "HandleFunc": "ANY",
	}
	if method, ok := httpMethods[funcName]; ok && len(call.Args) >= 2 && receiverName != "http" {
		pattern := stringLiteral(call.Args[0])
		if pattern != "" {
			handler := exprString(call.Args[1])
			routerType := "gorilla/mux"
			if strings.Contains(receiverName, "router") || strings.Contains(receiverName, "engine") {
				routerType = "gin"
			}
			return HTTPHandler{Pattern: pattern, Method: method, HandlerFunc: handler, RegistrationLine: line, RouterType: routerType}, true
		}
	}

	// gin-style uppercase: router.GET, router.POST etc.
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

// isHTTPClientReceiver returns true for receivers that are the standard
// net/http package or an http-specific client variable.
func isHTTPClientReceiver(name string) bool {
	lower := strings.ToLower(name)
	return lower == "http" ||
		lower == "http.defaultclient" ||
		(strings.Contains(lower, "http") && strings.Contains(lower, "client"))
}

// isClientLibReceiver returns true for receivers that look like a service
// client library variable (e.g. mcbsClient, identityClient).
func isClientLibReceiver(name string) bool {
	lower := strings.ToLower(name)
	return strings.Contains(lower, "client") || strings.HasSuffix(lower, "cli")
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
			s.Fields = append(s.Fields, StructField{Name: typStr, Type: typStr, JSONTag: jsonTag})
		}
		for _, fieldName := range field.Names {
			s.Fields = append(s.Fields, StructField{Name: fieldName.Name, Type: typStr, JSONTag: jsonTag})
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

// extractStringArgs returns all args rendered as strings (including non-literals).
func extractStringArgs(args []ast.Expr) []string {
	result := make([]string, 0, len(args))
	for _, arg := range args {
		result = append(result, exprString(arg))
	}
	return result
}

// extractOnlyStringLiterals returns only the string literal values, unquoted.
// Non-literal args are skipped. Used when we only want statically-known strings.
func extractOnlyStringLiterals(args []ast.Expr) []string {
	result := []string{}
	for _, arg := range args {
		if lit, ok := arg.(*ast.BasicLit); ok && lit.Kind == token.STRING {
			result = append(result, strings.Trim(lit.Value, `"`))
		}
	}
	return result
}

// extractStringLiteralsResolved returns string literal values, with variable
// references resolved through varMap. Used when capturing call-site strings
// for HTTP calls and call edges.
func extractStringLiteralsResolved(args []ast.Expr, varMap map[string]string) []string {
	result := []string{}
	for _, arg := range args {
		if v := stringLiteralResolved(arg, varMap); v != "" {
			result = append(result, v)
		}
	}
	return result
}

// stringLiteralResolved returns the string value of an expression: either a
// string literal directly, or a variable that was assigned a string literal
// (via varMap from collectVarAssignments).
func stringLiteralResolved(e ast.Expr, varMap map[string]string) string {
	switch a := e.(type) {
	case *ast.BasicLit:
		if a.Kind == token.STRING {
			return strings.Trim(a.Value, `"`)
		}
	case *ast.Ident:
		if v, ok := varMap[a.Name]; ok {
			return v
		}
	}
	return ""
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
