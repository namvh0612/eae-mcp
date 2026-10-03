# Function (POU) — reusable helper code

An IEC 61131-3 function: inputs → one return value, **no internal state, no events**. Use it for helper
calculations shared by many Basic FBs (formatting, conversions, array scans). File
`IEC61499/POU/<Name>.fct` (`<POUType>`, `Identification Standard="1131-3"`, `Comment="Function"`),
registered as `<Compile Include="POU\<Name>.fct"><IEC61499Type>Function</IEC61499Type>` plus
`POU\<Name>.doc.xml` (`DependentUpon`).

```xml
<InterfaceList ReturnValueType="INT">          <!-- empty string = no return value (VOID) -->
  <InputVars>        … VAR_INPUT  (by value)
  <OutputVars>       … VAR_OUTPUT
  <InputOutputVars>  … VAR_IN_OUT (by reference; may be ArraySize="*" = any length)
</InterfaceList>
<POUBasicFunction>
  <TempVars> … locals, re-initialised on every call (InitialValue allowed) </TempVars>
  <Algorithm Name="<same as the function>"><ST><![CDATA[ … ]]></ST></Algorithm>
</POUBasicFunction>
```

- Return a value by assigning to the function name: `HexToDecimal := decimalValue;`.
- Variables of user types carry `Namespace="SE.Agile"` (type namespace) on the `VarDeclaration`.
- EAE 26 writes `ID` on variables of new functions; older library functions have none.
- `ArraySize="*"` on an IN_OUT accepts arrays of any length; use `UPPER_BOUND(arr, 1)` / `LOWER_BOUND`.
- Call from ST in Basic FB algorithms or other functions, formal or positional:
  `s := FormatRealToString(Value := PV, Decimals := 2);` — IN_OUT arguments must be variables.
- Functions are not placed in FB networks; use a Basic FB when you need events or memory.

Library examples (SE.Agile): `IsDigit`, `IsAsciiLetter`, `FormatRealToString`, `FrequencyCheck_v1_0`,
the `Var*`/`Connection*` helpers that manage Broadcaster/Listener connection tables in IN_OUT arrays.
