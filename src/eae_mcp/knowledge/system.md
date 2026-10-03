# System: Applications, Devices, Resources, Mapping

`IEC61499/System/<sysId>.system` and its folder.

| Element | File | Meaning |
|---|---|---|
| Application | `<appId>.sysapp` | *What* runs, independent of hardware |
| Layer | `<appId>/<layerId>.syslay` | The application's FB network (`<SubAppNetwork>`), default layer "Default" |
| Device | `<devId>.sysdev` | A runtime target, e.g. `Type="Soft_dPAC" Namespace="SE.DPAC"` |
| Resource | `<devId>/<resId>.sysres` | Execution container on a device, e.g. `RES0 : EMB_RES_ECO` |
| Device properties | `<devId>/F513CAE3-….Properties.xml` | Deploy (`ClearBeforeDeploy`, `AutoStart`), Boot (`BootMode`), eHMI profile, security |

## Mapping
Mapping an application FB to a resource creates a **copy** in the resource network with a new ID and `Mapping="<application FB ID>"`; parameters are duplicated. A resource also needs a `DPAC_FULLINIT` to initialize before deployment.

## OPC UA exposure
Written twice, as `OPCUAAttribute Name="Exposed"` with an ID path context: in `<layer>/opcua.xml` (`<appFB>.<innerFB>.<var>`) and in `<resource>/opcua.xml` (`<resourceFB>.<innerFB>.<var>`).

## Build
Tools › Check Changes / Recheck All / Clean (IEC 61499), HMI › Build, eHMI › Build. Output goes to `*/bin`.
