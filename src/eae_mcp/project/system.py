"""System / Application / Layer / Device / Resource parsing.

Layout (relative to an IEC61499 project):
    System/<sysId>.system
    System/<sysId>/<appId>.sysapp
    System/<sysId>/<appId>/<layerId>.syslay        (+ <layerId>/opcua.xml, offline.xml, …)
    System/<sysId>/<devId>.sysdev
    System/<sysId>/<devId>/<resId>.sysres          (+ <resId>/opcua.xml, …)
    System/<sysId>/<devId>/F513CAE3-….Properties.xml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from ..model import Network
from .types import child, children, local, parse_network


@dataclass
class Layer:
    id: str
    name: str
    is_default: bool
    path: str
    network: Network | None = None


@dataclass
class Application:
    id: str
    name: str
    path: str
    layers: list[Layer] = field(default_factory=list)


@dataclass
class OpcUaExposure:
    context: str  # dotted ID path, e.g. <layer FB ID>.<inner FB ID>.<var ID>
    exposed: bool
    owner: str  # application or device ID
    file: str


@dataclass
class Resource:
    id: str
    name: str
    type: str
    namespace: str | None
    path: str
    network: Network | None = None


@dataclass
class Device:
    id: str
    name: str
    type: str
    namespace: str | None
    path: str
    resources: list[Resource] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    folder: str | None = None


@dataclass
class System:
    id: str
    name: str
    path: str
    applications: list[Application] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    opcua: list[OpcUaExposure] = field(default_factory=list)


def _root(path: Path):
    return xmlrt.load(path).root


def _flatten_properties(el, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for c in children(el):
        name = c.get("Name", local(c))
        key = f"{prefix}{name}"
        if local(c) == "Property":
            out[key] = c.get("Value", "")
        else:
            out.update(_flatten_properties(c, key + "/"))
    return out


def _opcua(path: Path, rel: str) -> list[OpcUaExposure]:
    if not path.exists():
        return []
    out = []
    for obj in children(_root(path), "OPCUAComplexObject"):
        for attr in children(obj, "OPCUAAttribute"):
            if attr.get("Name") == "Exposed" and attr.get("Context"):
                out.append(OpcUaExposure(attr.get("Context", ""), attr.get("Value") == "True", obj.get("UID", ""), rel))
    return out


def load_systems(project_dir: Path, rel_prefix: str = "") -> list[System]:
    """Find every *.system under <project>/System and load its tree."""
    systems = []
    sys_dir = project_dir / "System"
    if not sys_dir.is_dir():
        return systems

    def rel(p: Path) -> str:
        return (rel_prefix + p.relative_to(project_dir).as_posix()).lstrip("/")

    for sys_file in sorted(sys_dir.glob("*.system")):
        root = _root(sys_file)
        system = System(id=root.get("ID", sys_file.stem), name=root.get("Name", ""), path=rel(sys_file))
        sub = sys_dir / sys_file.stem
        for app_file in sorted(sub.glob("*.sysapp")):
            a = _root(app_file)
            app = Application(id=a.get("ID", app_file.stem), name=a.get("Name", ""), path=rel(app_file))
            for lay_file in sorted((sub / app_file.stem).glob("*.syslay")):
                lroot = _root(lay_file)
                app.layers.append(Layer(
                    id=lroot.get("ID", lay_file.stem),
                    name=lroot.get("Name", ""),
                    is_default=lroot.get("IsDefault") == "true",
                    path=rel(lay_file),
                    network=parse_network(child(lroot, "SubAppNetwork")),
                ))
                opc = sub / app_file.stem / lay_file.stem / "opcua.xml"
                system.opcua += _opcua(opc, rel(opc))
            system.applications.append(app)
        for dev_file in sorted(sub.glob("*.sysdev")):
            d = _root(dev_file)
            dev = Device(
                id=d.get("ID", dev_file.stem), name=d.get("Name", ""), type=d.get("Type", ""),
                namespace=d.get("Namespace"), path=rel(dev_file),
            )
            dev_dir = sub / dev_file.stem
            for props in sorted(dev_dir.glob("*.Properties.xml")):
                dev.properties.update(_flatten_properties(_root(props)))
            for res_file in sorted(dev_dir.glob("*.sysres")):
                r = _root(res_file)
                dev.resources.append(Resource(
                    id=r.get("ID", res_file.stem), name=r.get("Name", ""), type=r.get("Type", ""),
                    namespace=r.get("Namespace"), path=rel(res_file),
                    network=parse_network(child(r, "FBNetwork")),
                ))
                opc = dev_dir / res_file.stem / "opcua.xml"
                system.opcua += _opcua(opc, rel(opc))
            system.devices.append(dev)
        systems.append(system)
    return systems
