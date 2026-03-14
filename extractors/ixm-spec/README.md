# IXM Spec Extractor

Parses the IXM (Identity Exchange Message) XML specification and emits `SpecDoc` JSON for each message type. The IXM spec defines the XML message format used by the front door and back door services.

## What it extracts

For each top-level IXM message type:

- **Message identity**: type name, XML root element name, direction (inbound/outbound/both)
- **Fields**: complete field list with name, XML element path, data type, cardinality (one/many), required/optional, validation patterns (regex), allowed values (enumerations)
- **Description**: human-readable description of the message type and each field

## Why this matters

The front door service translates incoming IXM XML → internal JSON. The back door service does the reverse. Understanding the IXM spec is essential for:
- Debugging integration failures (malformed messages, validation errors)
- Understanding what data enters and exits the system
- Writing correct test messages
- Mapping between IXM field names and internal field names

The extractor makes IXM spec knowledge available for retrieval alongside code, schema, and docs.

## Input Format Support

### XSD (XML Schema Definition)

If the IXM spec is a formal XSD:

```xml
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="EnrollRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="BiometricID" type="xs:string" minOccurs="1" maxOccurs="1"/>
        <xs:element name="Modality" minOccurs="1" maxOccurs="1">
          <xs:simpleType>
            <xs:restriction base="xs:string">
              <xs:enumeration value="FINGERPRINT"/>
              <xs:enumeration value="IRIS"/>
            </xs:restriction>
          </xs:simpleType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
```

### Custom XML Format

If the spec uses a custom format (common for proprietary specs):

```xml
<IXMSpec version="3.2">
  <MessageTypes>
    <MessageType name="EnrollRequest" direction="inbound">
      <Description>Request to enroll a biometric subject</Description>
      <Fields>
        <Field name="BiometricID" type="string" required="true">
          <ValidationRule pattern="[0-9a-f-]{36}"/>
          <Description>UUID of the subject to enroll</Description>
        </Field>
        <Field name="Modality" type="enum" required="true">
          <AllowedValues>
            <Value>FINGERPRINT</Value>
            <Value>IRIS</Value>
            <Value>FACE</Value>
          </AllowedValues>
        </Field>
      </Fields>
    </MessageType>
  </MessageTypes>
</IXMSpec>
```

The extractor auto-detects the format by checking for the `xs:schema` namespace. For custom formats, XPath expressions can be configured to match the specific structure.

## Configuration

```yaml
ixm_spec:
  spec_dir: /path/to/ixm-spec/
  # Automatically detected; override if needed:
  format: auto    # Options: auto, xsd, custom
  # For custom format, configure XPath expressions:
  custom_xpath:
    message_types: "//MessageType"
    message_name_attr: "@name"
    message_direction_attr: "@direction"
    fields: "Fields/Field"
    field_name_attr: "@name"
    field_type_attr: "@type"
    field_required_attr: "@required"
    validation_pattern: "ValidationRule/@pattern"
    allowed_values: "AllowedValues/Value"
    description: "Description/text()"
```

## Output

One `SpecDoc` JSON file per message type, written to `extracted/ixm-spec/<MessageType>.json`.

```json
{
  "message_type": "EnrollRequest",
  "xml_root_element": "EnrollRequest",
  "direction": "inbound",
  "description": "Request to enroll a new biometric subject into the identity system",
  "fields": [
    {
      "name": "BiometricID",
      "xml_element": "BiometricID",
      "data_type": "string",
      "required": true,
      "cardinality": "one",
      "validation_pattern": "[0-9a-f-]{36}",
      "allowed_values": [],
      "description": "UUID of the biometric subject"
    },
    {
      "name": "Modality",
      "xml_element": "Modality",
      "data_type": "enum",
      "required": true,
      "cardinality": "one",
      "validation_pattern": null,
      "allowed_values": ["FINGERPRINT", "IRIS", "FACE"],
      "description": "Biometric modality of the template"
    }
  ]
}
```

## Running

```bash
python scripts/run_extractors.py --config config/config.yaml --extractor ixm-spec
```

## Notes

- The IXM spec file(s) must be available before transfer to the restricted network. Export them as part of the build phase.
- If the spec has multiple versions, configure the extractor to use the current/production version.
- Field names in the IXM spec may differ from internal JSON field names. The extractor captures the spec names; mapping to internal names requires manual curation or can be inferred from the front-door service code.
