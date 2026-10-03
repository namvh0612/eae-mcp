# Composite Function Block

A reusable FB whose body is a **network of FB instances**. File `<Name>.fbt` with `<FBNetwork>`, `IEC61499Type=Composite`, `Format="2.0"`.

- The interface is exposed inside the network as boundary pins `<Input>/<Output>` that reuse the interface IDs.
- Connections: `<EventConnections>`, `<DataConnections>`, `<AdapterConnections>`.
  - `$13D6AB69689E316D` → a boundary pin
  - `$A18F7D824841FE4E.C5791102B0BF17C2` → pin of a user-type instance (both IDs)
  - `$B928CB0DDA52358C.START` → pin of a library instance (by name)
- Parameters on instances: `<Parameter Name="$<VarID>" Value="5"/>` or `Name="$DT"` for library types.
- **Generic FBs** (e.g. `ADD` with N inputs) appear as `ADD_1990CFD1468AAE4A6` plus `Configuration.GenericFBType.InterfaceParams`.

Composite vs CAT: a CAT is a composite that also owns HMI, OPC UA and offline configuration. Composite vs SubApp: a composite is a reusable type; a SubApp only groups application content.
