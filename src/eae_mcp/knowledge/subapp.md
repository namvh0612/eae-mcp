# SubApp (Subapplication)

A SubApp **groups part of an application** to keep large applications readable. In EAE 26 it is packaging, **not a reusable type**: only FBs, adapters, datatypes, functions and CATs are reusable.

- Created inside an application by grouping FBs. EAE stores the grouped content in `IEC61499/<Name>/<Name>.app` (`<SubAppType>`, `IEC61499Type=SubApp`) together with `.subapp.offline.xml`/`.subapp.opcua.xml`, and the layer references it as `<SubApp ID Name Type="<Name>"/>`.
- Its interface uses SubApp-specific elements: `<SubAppInterfaceList><SubAppEventInputs><SubAppEvent …/>`.
- The network root is `<SubAppNetwork>` (the same element the application layer uses).
