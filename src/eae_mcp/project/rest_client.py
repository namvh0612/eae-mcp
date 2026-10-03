"""Generate a REST/HTTP client CAT, hand-built HTTP over NETIO (knowledge/rest-client.md).

Network of the generated CAT `<Name>`:

    INIT ─► Poll (E_CYCLE period) ─► Req (<Name>_Request: builds the HTTP request text)
    REQ  ────────────────────────────┘          │ CNF(Package)
                                                ▼
          Rsp.INIT ◄── Req.CNF ──► Net.INIT (NETIO, opens ENDPOINT, TLS with SNI)
                                    Net.INITO ─► Gate (E_PERMIT on Net.QO) ─► Net.REQ (sends Package)
          Net.IND(RD, RD_LEN) ─► Rsp.REQ ─► NEXT ─► Net.ACK        (one IND per received chunk)
                                               └─► CNF(Status, fields…) ─► CAT CNF, IThis.REQ (HMI)

`<Name>_Response` accumulates the answer, parses the status line, Content-Length or chunked encoding and
extracts JSON values with `<Name>_Json` (dotted key path + occurrence; see `extract`, its Python twin).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from ..io import xmlrt
from ..model import Algorithm, ECAction, ECState, ECTransition, Event, Interface, Var
from . import writer as w
from .cat_edit import create_cat
from .changes import ChangeSet
from .edit import EditError, _register, _taken_ids, target_project
from .solution import Solution
from .types import child, children, local, parse_type_element

NETIO_TYPE = "NETIO_16308C75BF8BAF741"
NETIO_PARAMS = "Runtime.IoCommon#I:=1;SD:STRING;RD:STRING"
BUF = 4096  # response buffer (bytes), as in the sample
VALUE_LEN = 255
FIELD_TYPES = {"REAL": "STRING_TO_REAL", "LREAL": "STRING_TO_LREAL", "DINT": "STRING_TO_DINT",
               "INT": "STRING_TO_INT", "BOOL": None, "STRING": None}


@dataclass
class RestField:
    name: str  # CAT output and HMI variable
    path: str  # dotted JSON keys, e.g. "data.price"; each key is searched after the previous one
    type: str = "REAL"
    occurrence: int = 1  # n-th match of the last key (arrays of objects)


@dataclass
class RestClientSpec:
    name: str
    host: str
    path: str = "/"
    method: str = "GET"
    port: int = 443
    tls: bool = True
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    auth: str = "bearer"  # bearer | none | header:<Name>  (token comes from the CAT input Token)
    fields: list[RestField] = field(default_factory=list)
    period: str = "T#5m"
    endpoint: str | None = None  # NETIO endpoint, default 'TCPS:;<host>:<port>' (or TCP)
    folder: str | None = ".RestApi"
    library: str | None = None


# -- ST helpers --------------------------------------------------------------------------------


def st_literal(text: str) -> str:
    """Python text → ST single-quoted string literal ($ escapes; CR/LF as $R/$N)."""
    out = text.replace("$", "$$").replace("'", "$'").replace("\r", "$R").replace("\n", "$N").replace("\t", "$T")
    return f"'{out}'"


def check_spec(spec: RestClientSpec) -> None:
    w.check_identifier(spec.name, "type name")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", spec.host):
        raise EditError(f"host must be a plain host name, got '{spec.host}'.")
    if not spec.path.startswith("/") or any(c in spec.path for c in " \r\n"):
        raise EditError("path must start with '/' and contain no spaces or line breaks (URL-encode them).")
    if spec.method.upper() not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        raise EditError("method must be GET, POST, PUT, PATCH or DELETE.")
    if spec.auth not in ("bearer", "none") and not re.fullmatch(r"header:[A-Za-z0-9-]+", spec.auth):
        raise EditError("auth must be 'bearer', 'none' or 'header:<Header-Name>'.")
    for k, v in spec.headers.items():
        if not re.fullmatch(r"[A-Za-z0-9-]+", k) or any(c in v for c in "\r\n"):
            raise EditError(f"Invalid header {k!r}.")
        if k.lower() in ("authorization", "x-api-key", "api-key") and v:
            raise EditError(f"Do not put secrets in headers ({k}); use auth and the CAT input Token.")
    if not spec.fields:
        raise EditError("Give at least one field to extract from the JSON answer.")
    names = set()
    for f in spec.fields:
        w.check_identifier(f.name, "field name")
        if f.name.upper() in ("STATUS", "HTTPSTATUS", "TOKEN", "ENDPOINT", "QI", "QO", "INIT", "REQ", "CNF", "INITO", "NEXT") or f.name in names:
            raise EditError(f"Field name '{f.name}' is reserved or duplicated.")
        names.add(f.name)
        if f.type.upper() not in FIELD_TYPES:
            raise EditError(f"Field {f.name}: type must be one of {', '.join(FIELD_TYPES)}.")
        if not re.fullmatch(r"[^.\"$']+(\.[^.\"$']+)*", f.path) or len(f.path) > 120:
            raise EditError(f"Field {f.name}: path must be dotted JSON keys without quotes or '$'.")
        if f.occurrence < 1:
            raise EditError(f"Field {f.name}: occurrence starts at 1.")
    if not re.fullmatch(r"T#\d+(ms|s|m|h)", spec.period):
        raise EditError("period must look like T#30s, T#5m or T#1h.")


def request_text(spec: RestClientSpec, token: str = "<Token>") -> str:
    lines = [f"{spec.method.upper()} {spec.path} HTTP/1.1", f"Host: {spec.host}"]
    if spec.auth == "bearer":
        lines.append(f"Authorization: Bearer {token}")
    elif spec.auth.startswith("header:"):
        lines.append(f"{spec.auth.split(':', 1)[1]}: {token}")
    lines.append("Accept: application/json")
    lines += [f"{k}: {v}" for k, v in spec.headers.items()]
    if spec.body is not None:
        lines += ["Content-Type: application/json", f"Content-Length: {len(spec.body.encode())}"]
    lines.append("Connection: close")
    return "\r\n".join(lines) + "\r\n\r\n" + (spec.body or "")


def _request_st(spec: RestClientSpec) -> tuple[str, int]:
    """ST that builds Package; returns (code, Package length)."""
    full = request_text(spec, "\x00")
    before, _, after = full.partition("\x00")
    if spec.auth == "none":
        code = f"Package := {st_literal(full)};\n"
        return code, len(full)
    chunks = [before[i:i + 200] for i in range(0, len(before), 200)] or [""]
    code = f"Package := {st_literal(chunks[0])};\n"
    for c in chunks[1:]:
        code += f"Package := CONCAT(Package, {st_literal(c)});\n"
    code += "Package := CONCAT(Package, Token);\n"
    for i in range(0, len(after), 200):
        code += f"Package := CONCAT(Package, {st_literal(after[i:i + 200])});\n"
    return code, len(before) + 128 + len(after)


JSON_FN = """(* Value of the JSON key path Path (dotted keys, each searched after the previous one); Occurrence
   selects the n-th match of the last key. Strings are returned without quotes; '' when not found. *)
found := TRUE;
pos := 1;
rest := Path;
WHILE found AND (LEN(rest) > 0) DO
    dot := FIND(rest, '.');
    IF dot > 0 THEN
        seg := LEFT(rest, dot - 1);
        rest := RIGHT(rest, LEN(rest) - dot);
    ELSE
        seg := rest;
        rest := '';
    END_IF;
    key := CONCAT('"', CONCAT(seg, '"'));
    IF LEN(rest) = 0 THEN
        n := Occurrence;
    ELSE
        n := 1;
    END_IF;
    FOR i := 1 TO n DO
        IF found THEN
            hit := FIND(RIGHT(Body, LEN(Body) - pos + 1), key);
            IF hit = 0 THEN
                found := FALSE;
            ELSE
                pos := pos + hit - 1 + LEN(key);
            END_IF;
        END_IF;
    END_FOR;
END_WHILE;
value := '';
IF found THEN
    (* skip ':' and blanks *)
    WHILE pos <= LEN(Body) DO
        c := MID(Body, 1, pos);
        IF (c = ':') OR (c = ' ') OR (c = '$T') OR (c = '$R') OR (c = '$N') THEN
            pos := pos + 1;
        ELSE
            EXIT;
        END_IF;
    END_WHILE;
    IF MID(Body, 1, pos) = '"' THEN
        hit := FIND(RIGHT(Body, LEN(Body) - pos), '"');
        IF hit > 1 THEN
            value := MID(Body, MIN(hit - 1, @VLEN@), pos + 1);
        END_IF;
    ELSE
        e := pos;
        WHILE e <= LEN(Body) DO
            c := MID(Body, 1, e);
            IF (c = ',') OR (c = '}') OR (c = ']') OR (c = ' ') OR (c = '$R') OR (c = '$N') THEN
                EXIT;
            END_IF;
            e := e + 1;
        END_WHILE;
        IF e > pos THEN
            value := MID(Body, MIN(e - pos, @VLEN@), pos);
        END_IF;
    END_IF;
END_IF;
@FN@ := value;
"""

RX_ALG = """(* Accumulate one chunk from NETIO and decide whether the answer is complete. *)
done := FALSE;
IF RD_LEN = 0 THEN
    done := TRUE; (* connection closed by the server *)
ELSIF LEN(buf) + UINT_TO_INT(RD_LEN) > @BUF@ THEN
    Status := -4; (* answer larger than the buffer *)
    done := TRUE;
ELSE
    buf := CONCAT(buf, LEFT(RD, UINT_TO_INT(RD_LEN)));
END_IF;
IF NOT hdrDone THEN
    hdrEnd := FIND(buf, '$R$N$R$N');
    IF hdrEnd > 0 THEN
        hdrDone := TRUE;
        IF FIND(buf, 'HTTP/1.') = 1 THEN
            Status := STRING_TO_INT(MID(buf, 3, 10));
        ELSE
            Status := -2; (* not an HTTP answer *)
            done := TRUE;
        END_IF;
        line := LEFT(buf, hdrEnd);
        chunked := (FIND(line, 'ransfer-Encoding: chunked') > 0) OR (FIND(line, 'ransfer-encoding: chunked') > 0);
        p := FIND(line, 'ontent-Length: ');
        IF p = 0 THEN
            p := FIND(line, 'ontent-length: ');
        END_IF;
        contentLength := -1;
        IF p > 0 THEN
            line := RIGHT(line, LEN(line) - p - 14);
            q := FIND(line, '$R');
            IF q > 0 THEN
                line := LEFT(line, q - 1);
            END_IF;
            contentLength := STRING_TO_INT(line);
        END_IF;
    END_IF;
END_IF;
IF hdrDone AND NOT done THEN
    IF chunked THEN
        done := FIND(buf, '$R$N0$R$N') > 0;
    ELSIF contentLength >= 0 THEN
        done := (LEN(buf) - hdrEnd - 3) >= contentLength;
    END_IF;
END_IF;
"""

PARSE_ALG = """(* Body of the answer, de-chunked, then the configured fields. *)
body := '';
IF hdrDone THEN
    body := RIGHT(buf, LEN(buf) - hdrEnd - 3);
END_IF;
IF chunked THEN
    raw := body;
    body := '';
    p := 1;
    more := TRUE;
    WHILE more AND (p < LEN(raw)) DO
        q := FIND(RIGHT(raw, LEN(raw) - p + 1), '$R$N');
        IF q < 2 THEN
            more := FALSE;
        ELSE
            size := 0;
            FOR k := 1 TO q - 1 DO
                c := MID(raw, 1, p + k - 1);
                d := FIND('0123456789abcdef', c);
                IF d = 0 THEN
                    d := FIND('0123456789ABCDEF', c);
                END_IF;
                IF d > 0 THEN
                    size := size * 16 + d - 1;
                END_IF;
            END_FOR;
            IF size = 0 THEN
                more := FALSE;
            ELSE
                body := CONCAT(body, MID(raw, size, p + q + 1));
                p := p + q + 1 + size + 2;
            END_IF;
        END_IF;
    END_WHILE;
END_IF;
IF (Status >= 200) AND (Status < 300) THEN
@FIELDS@END_IF;
"""

INIT_ALG = """buf := '';
hdrDone := FALSE;
chunked := FALSE;
contentLength := -1;
hdrEnd := 0;
Status := 0;
"""


def _field_st(fn: str, f: RestField) -> str:
    call = f"{fn}(Body := body, Path := {st_literal(f.path)}, Occurrence := {f.occurrence})"
    t = f.type.upper()
    if t == "STRING":
        return f"    {f.name} := {call};\n"
    if t == "BOOL":
        return f"    {f.name} := {call} = 'true';\n"
    return f"    {f.name} := {FIELD_TYPES[t]}({call});\n"


def _field_var(f: RestField) -> Var:
    t = f.type.upper()
    return Var(f.name, f"STRING[{VALUE_LEN}]" if t == "STRING" else t)


# -- Python twin of the ST extractor (used by the probe and by tests) ---------------------------------


def extract(body: str, path: str, occurrence: int = 1) -> str:
    pos = 0
    segs = path.split(".")
    for i, seg in enumerate(segs):
        key = f'"{seg}"'
        for _ in range(occurrence if i == len(segs) - 1 else 1):
            hit = body.find(key, pos)
            if hit < 0:
                return ""
            pos = hit + len(key)
    while pos < len(body) and body[pos] in ": \t\r\n":
        pos += 1
    if pos < len(body) and body[pos] == '"':
        end = body.find('"', pos + 1)
        return body[pos + 1:end][:VALUE_LEN] if end > pos + 1 else ""
    end = pos
    while end < len(body) and body[end] not in ",}] \r\n":
        end += 1
    return body[pos:end][:VALUE_LEN]


# -- generator --------------------------------------------------------------------------------------


def _ecc_request() -> tuple[list[ECState], list[ECTransition]]:
    return ([ECState("START", "Initial State"), ECState("BUILD", actions=[ECAction("Build", "CNF")])],
            [ECTransition("START", "BUILD", "REQ"), ECTransition("BUILD", "START", "1")])


def _ecc_response() -> tuple[list[ECState], list[ECTransition]]:
    return ([ECState("START", "Initial State"), ECState("INIT", actions=[ECAction("Init", None)]),
             ECState("RX", actions=[ECAction("Rx", None)]), ECState("MORE", actions=[ECAction(None, "NEXT")]),
             ECState("DONE", actions=[ECAction("Parse", "CNF")])],
            [ECTransition("START", "INIT", "INIT"), ECTransition("INIT", "START", "1"),
             ECTransition("START", "RX", "REQ"), ECTransition("RX", "DONE", "done"),
             ECTransition("RX", "MORE", "NOT done"), ECTransition("MORE", "START", "1"),
             ECTransition("DONE", "START", "1")])


def create_rest_client(sol: Solution, spec: RestClientSpec) -> ChangeSet:
    check_spec(spec)
    spec = copy.deepcopy(spec)
    n = spec.name
    for suffix in ("", "_Request", "_Response", "_Json", "_HMI"):
        if sol.find_type(f"{n}{suffix}") is not None:
            raise EditError(f"A type named {n}{suffix} already exists.")
    t = target_project(sol, spec.library)
    token = spec.auth != "none"
    fields = spec.fields

    # CAT interface and HMI interface.
    outs = [Var("QO", "BOOL", comment="Output event qualifier"), Var("Status", "INT", comment="HTTP status or <0 error")]
    outs += [_field_var(f) for f in fields]
    ins = [Var("QI", "BOOL", comment="Input event qualifier")]
    if token:
        ins.append(Var("Token", "STRING[128]", comment="API token (set at runtime; never stored in the type)"))
    ins.append(Var("Endpoint", "STRING[80]", comment="NETIO endpoint, e.g. 'TCPS:;<ip>:443'",
                   initial_value=st_literal(spec.endpoint or f"{'TCPS' if spec.tls else 'TCP'}:;{spec.host}:{spec.port}")))
    cat_itf = Interface(
        event_inputs=[Event("INIT", comment="Start polling", with_vars=["QI"] + (["Token"] if token else []) + ["Endpoint"]),
                      Event("REQ", comment="Request now")],
        event_outputs=[Event("INITO", comment="Initialization Confirm", with_vars=["QO"]),
                       Event("CNF", comment="Answer received", with_vars=["Status"] + [f.name for f in fields])],
        input_vars=ins, output_vars=outs)
    hmi = Interface(event_inputs=[Event("REQ", with_vars=["HttpStatus"] + [f.name for f in fields])],
                    input_vars=[Var("HttpStatus", "INT")] + [Var(f.name, "STRING" if f.type.upper() == "STRING"
                                                             else f.type.upper()) for f in fields])
    cs = create_cat(sol, n, cat_itf, hmi, folder=spec.folder, library=spec.library,
                    comment=f"REST client for {spec.host}{spec.path.split('?')[0]}")

    # Helper function and the two basic FBs.
    fn = f"{n}_Json"
    json_root_vars = dict(
        inputs=[Var("Path", "STRING[128]"), Var("Occurrence", "INT")],
        inouts=[Var("Body", f"STRING[{BUF}]")],
        temps=[Var("found", "BOOL"), Var("pos", "INT"), Var("hit", "INT"), Var("dot", "INT"), Var("i", "INT"),
               Var("n", "INT"), Var("e", "INT"), Var("rest", "STRING[128]"), Var("seg", "STRING[128]"),
               Var("key", "STRING[130]"), Var("c", "STRING[1]"), Var("value", f"STRING[{VALUE_LEN}]")])
    from .edit import _function_root
    code = JSON_FN.replace("@FN@", fn).replace("@VLEN@", str(VALUE_LEN))
    froot = _function_root(fn, t.namespace, json_root_vars["inputs"], [], json_root_vars["inouts"],
                           f"STRING[{VALUE_LEN}]", json_root_vars["temps"], code, None)
    frel = f"{t.dir}POU/{fn}.fct"
    cs.create(frel, xmlrt.dumps(xmlrt.new_document(froot, w.DOCTYPE.format(root="POUType"))))
    cs.create(f"{t.dir}POU/{fn}.doc.xml", w.template("doc.xml"))
    proj = cs.doc(t.dfbproj)
    w.add_project_item(proj, "Compile", f"POU\\{fn}.fct", [("IEC61499Type", "Function")])
    w.add_project_item(proj, "None", f"POU\\{fn}.doc.xml", [("DependentUpon", f"{fn}.fct")])

    req_code, pkg_len = _request_st(spec)
    pkg_len = max(64, pkg_len + 16)
    if pkg_len > 2048:
        raise EditError(f"The request is {pkg_len} characters; keep it under 2048 (shorter body/headers).")
    req_itf = Interface(event_inputs=[Event("REQ", with_vars=["Token"] if token else [])],
                        event_outputs=[Event("CNF", with_vars=["Package"])],
                        input_vars=[Var("Token", "STRING[128]")] if token else [],
                        output_vars=[Var("Package", f"STRING[{pkg_len}]")])
    st, tr = _ecc_request()
    req_bytes = w.build_basic(f"{n}_Request", req_itf, [], st, tr, [Algorithm("Build", req_code)], t.namespace,
                              f"Builds the HTTP request for {spec.host}")
    rsp_itf = Interface(event_inputs=[Event("INIT"), Event("REQ", with_vars=["RD", "RD_LEN"])],
                        event_outputs=[Event("NEXT"), Event("CNF", with_vars=["Status"] + [f.name for f in fields])],
                        input_vars=[Var("RD", "STRING[1024]"), Var("RD_LEN", "UINT")],
                        output_vars=[Var("Status", "INT")] + [_field_var(f) for f in fields])
    internals = [Var("buf", f"STRING[{BUF}]"), Var("body", f"STRING[{BUF}]"), Var("raw", f"STRING[{BUF}]"),
                 Var("line", "STRING[1024]"), Var("hdrDone", "BOOL"), Var("chunked", "BOOL"), Var("done", "BOOL"),
                 Var("more", "BOOL"), Var("hdrEnd", "INT"), Var("contentLength", "INT"), Var("p", "INT"),
                 Var("q", "INT"), Var("k", "INT"), Var("d", "INT"), Var("size", "INT"), Var("c", "STRING[1]")]
    fields_st = "".join(_field_st(fn, f) for f in fields)
    st, tr = _ecc_response()
    rsp_bytes = w.build_basic(f"{n}_Response", rsp_itf, internals, st, tr,
                              [Algorithm("Init", INIT_ALG), Algorithm("Rx", RX_ALG.replace("@BUF@", str(BUF))),
                               Algorithm("Parse", PARSE_ALG.replace("@FIELDS@", fields_st))],
                              t.namespace, f"Parses the HTTP answer from {spec.host}")
    for name, data in ((f"{n}_Request", req_bytes), (f"{n}_Response", rsp_bytes)):
        cs.create(f"{t.dir}{name}.fbt", data)
        cs.create(f"{t.dir}{name}.doc.xml", w.template("doc.xml"))
        cs.create(f"{t.dir}{name}.meta.xml", w.template("meta.xml"))
        _register(cs, t, "basic", f"{name}.fbt", [f"{name}.doc.xml", f"{name}.meta.xml"], spec.folder)

    _wire(cs, t, n, spec, req_bytes, rsp_bytes, token)
    cs.commit_docs()
    cs.description = f"create REST client CAT {n} for {spec.method.upper()} https://{spec.host}{spec.path}"
    cs.warnings.append("info: set the API token on the instance input Token at runtime (OPC UA/HMI/offline "
                       "parameter); it is never written into the type files.")
    if not spec.endpoint:
        cs.warnings.append("info: Endpoint defaults to the host name; if your NETIO needs an IP, set Endpoint to "
                           "'TCPS:;<ip>:443' or resolve it first (DNSHostQuery pattern, eae_knowledge 'rest client').")
    return cs


def _ids(data: bytes, rel: str):
    td = parse_type_element(xmlrt.parse_bytes(data).root, rel)
    pins = {}
    for e in td.interface.event_inputs + td.interface.event_outputs:
        pins[e.name] = e.id
    for v in td.interface.input_vars + td.interface.output_vars:
        pins[v.name] = v.id
    return pins


def _wire(cs: ChangeSet, t, n: str, spec: RestClientSpec, req_bytes: bytes, rsp_bytes: bytes, token: bool) -> None:
    cat_rel = f"{t.dir}{n}/{n}.fbt"
    xf = xmlrt.parse_bytes(cs.changes[cat_rel].new)
    root = xf.root
    net = child(root, "FBNetwork")
    taken = _taken_ids(root)
    from ..io.ids import new_id16

    cat = _ids(cs.changes[cat_rel].new, cat_rel)
    ithis = _ids(cs.changes[f"{t.dir}{n}/{n}_HMI.fbt"].new, "hmi")
    req = _ids(req_bytes, "req")
    rsp = _ids(rsp_bytes, "rsp")
    ithis_el = next(e for e in children(net, "FB") if e.get("Name") == "IThis")
    ithis_id = ithis_el.get("ID")
    ithis_el.set("x", "5200")
    ithis_el.set("y", "1400")
    ns = t.namespace

    def fb(name: str, type_: str, x: int, y: int, namespace: str, params: list[tuple[str, str]],
           attr: str | None = None) -> str:
        fid = new_id16(taken)
        el = w._el("FB", [("ID", fid), ("Name", name), ("Type", type_), ("x", str(x)), ("y", str(y)),
                          ("Namespace", namespace)])
        if attr:
            w._sub(el, "Attribute", [("Name", "Configuration.GenericFBType.InterfaceParams"), ("Value", attr)])
        for k, v in params:
            w._sub(el, "Parameter", [("Name", k), ("Value", v)])
        fbs = [i for i, e in enumerate(children(net)) if local(e) == "FB"]
        xmlrt.insert_child(net, el, fbs[-1] + 1 if fbs else 0)
        return fid

    poll = fb("Poll", "E_CYCLE", 900, 400, "IEC61499.Standard", [("$DT", spec.period)])
    rq = fb("Req", f"{n}_Request", 1700, 400, ns, [])
    netio = fb("Net", NETIO_TYPE, 2600, 900, ns,
               [("$QI", "TRUE")] + ([("$SNI", st_literal(spec.host))] if spec.tls else []), NETIO_PARAMS)
    gate = fb("Gate", "E_PERMIT", 3500, 500, "IEC61499.Standard", [])
    rs = fb("Rsp", f"{n}_Response", 4200, 1000, ns, [])

    def b(pin: str) -> str:  # CAT boundary pin
        return f"${cat[pin]}"

    events = [
        (b("INIT"), f"${poll}.START"), (b("INIT"), f"${rq}.{req['REQ']}"), (b("INIT"), f"${ithis_id}.{ithis['INIT']}"),
        (b("REQ"), f"${rq}.{req['REQ']}"), (f"${poll}.EO", f"${rq}.{req['REQ']}"),
        (f"${rq}.{req['CNF']}", f"${netio}.INIT"), (f"${rq}.{req['CNF']}", f"${rs}.{rsp['INIT']}"),
        (f"${netio}.INITO", f"${gate}.EI"), (f"${gate}.EO", f"${netio}.REQ"),
        (f"${netio}.IND", f"${rs}.{rsp['REQ']}"), (f"${rs}.{rsp['NEXT']}", f"${netio}.ACK"),
        (f"${rs}.{rsp['CNF']}", b("CNF")), (f"${rs}.{rsp['CNF']}", f"${ithis_id}.{ithis['REQ']}"),
        (f"${ithis_id}.{ithis['INITO']}", b("INITO")),
    ]
    data = [
        (f"${rq}.{req['Package']}", f"${netio}.SD"), (b("Endpoint"), f"${netio}.ENDPOINT"),
        (f"${netio}.QO", f"${gate}.PERMIT"), (f"${netio}.RD", f"${rs}.{rsp['RD']}"),
        (f"${netio}.RD_LEN", f"${rs}.{rsp['RD_LEN']}"), (f"${ithis_id}.{ithis['QO']}", b("QO")),
        (f"${rs}.{rsp['Status']}", b("Status")), (f"${rs}.{rsp['Status']}", f"${ithis_id}.{ithis['HttpStatus']}"),
    ]
    if token:
        data.append((b("Token"), f"${rq}.{req['Token']}"))
    for f in spec.fields:
        data.append((f"${rs}.{rsp[f.name]}", b(f.name)))
        data.append((f"${rs}.{rsp[f.name]}", f"${ithis_id}.{ithis[f.name]}"))
    for tag, conns in (("EventConnections", events), ("DataConnections", data)):
        sec = child(net, tag)
        if sec is None:
            sec = w._sub(net, tag)
        for s, d in conns:
            w._sub(sec, "Connection", [("Source", s), ("Destination", d)])
    from .changes import FileChange
    cs.changes[cat_rel] = FileChange(cat_rel, None, xmlrt.dumps(xf))
