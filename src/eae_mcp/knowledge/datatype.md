# DataType

A user-defined data type, file `IEC61499/DataType/<Name>.dt`, registered in `.dfbproj` as `IEC61499Type=DataType`.

## Kinds offered by EAE 26
| Kind | ST definition | XML |
|---|---|---|
| Structure | `TYPE dtS : STRUCT A : INT; B : REAL; END_STRUCT END_TYPE` | `<StructuredType><VarDeclaration Name Type/>…` |
| Enumeration | `TYPE dtE : USINT (Stop := 0, Run := 1, Fault := 2); END_TYPE` | `<EnumeratedType Type="USINT"><EnumeratedValue Name Value/>…` |
| Array | `TYPE dtA : ARRAY[0..9] OF INT; END_TYPE` | `<ArrayType BaseType="INT"><Subrange LowerLimit UpperLimit/>` |
| Subrange | `TYPE dtR : INT (0..100); END_TYPE` | `<SubrangeType BaseType="INT"><Subrange …/>` |

There is no Alias kind.

## Things to know
- The editor is **ST text**. A syntax error blocks the XML update ("Syntax error detected. XML will not be updated.").
- Enum values use `:=`, not `=`. Some words are reserved (e.g. `On`), so pick names like `Stop/Run/Fault`.
- Every `.dt` carries `Attribute nxtDataType` (an encrypted blob). EAE regenerates it on the next edit and builds fine without it.
- Struct members have no IDs (FB variables do).
