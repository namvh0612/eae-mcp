# System libraries and the library store

Projects reference system libraries by name and version in `.dfbproj`:
`<Reference Include="Runtime.Base"><Version>26.0.0.7</Version></Reference>`.

They are installed in `C:\ProgramData\Schneider Electric\Libraries\<Lib>-<Version>\`:
`Files\<Namespace>\<Type>.{fbt,adp,dt,fct,res,dev}` (readable interfaces; bodies encrypted in `nxtLibraryData`), compiled DLLs in `IEC61499\`, HMI assets, docs. Several versions coexist.

Common namespaces: `IEC61499.Standard` (E_CYCLE, E_DELAY, E_PERMIT, …), `Runtime.Standard`, `Runtime.Management` (EMB_RES_ECO), `SE.DPAC` (Soft_dPAC). Library pins have no IDs, so networks reference them by name.
