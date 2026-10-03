# Adapter

An adapter type bundles **events and data in both directions** into one connectable pin. File `<Name>.adp`, `IEC61499Type=Adapter`.

- Defined from the **plug** side: `EventInputs`/`InputVars` flow socket → plug, `EventOutputs`/`OutputVars` flow plug → socket. The `<Service>` block (request_confirm / indication_response) is a fixed template.
- An FB exposes adapters as pins:
  - Basic FB: `<Sockets>` / `<Plugs>` containing `<AdapterDeclaration Name Type Namespace/>`. Inside ST, adapter members are accessed as `Plant.Value`.
  - Composite/CAT: `<AdapterInputs>` / `<AdapterOutputs>` containing `<Adapter Name Type Namespace/>`, plus a boundary pin `<Input|Output Type="Adapter"/>` in the network.
- Connections are in `<AdapterConnections>`, one line instead of many event + data wires. Adapter pins are referenced by **name**.

## Typical use (SE.Agile pattern)
`BroadcasterGrid_v1_0` has an adapter output `ISocket : aGrid_v1_0`; `ListenerGrid_v1_0` has an adapter input `IPlug : aGrid_v1_0`. The CAT `acPlant_v1_0` connects its basic FB's plugs to listeners so one equipment publishes its state to others.
