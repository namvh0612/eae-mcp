# REST/HTTP client in EAE (hand-built HTTP over NETIO)

EAE has no ready-made HTTP client block: a CAT builds HTTP/1.1 by hand on top of the
generic socket block **NETIO** (Runtime.IoCommon). The pattern works for any REST API that returns a small
JSON body.

## Network of a typical REST client CAT (folder `.RestApi`)

```
REQ ─► DNSHostQuery ──CNF_IP(IPAddress='TCPS:;<ip>:443')──► PackageBuilder ──Package──► NETIO (HTTP)
            (UDP DNS to 8.8.8.8:53 via its own NETIO)          ▲ REQ (ApiKey)            │ INITO/QO
                                                               │                         ▼
                                    E_CYCLE T#5m ──────────────┘          E_PERMIT ──REQ──► NETIO.REQ (send SD)
NETIO.IND(RD, RD_LEN) ─► PackageHandler.REQ ──NEXT──► NETIO.ACK   (one IND per received chunk ≤ 1024 B)
PackageHandler.CNF(rows) ─► PackageFilter ─► CNF ─► IThis (HMI) + adapter plug IDataUpdate (aPricingUpdate_v1_0)
```

| Block | Role |
|---|---|
| DNSHostQuery (composite) → DNSQueryLogic (basic) + NETIO `BYTE[256]/BYTE[1024]` | resolves the host: hand-built DNS query over `UDP:;<Gateway>:53`; result `TCPS:;<ip>:<port>` (TLS) or `TCP:;…` |
| PackageBuilder (basic) | builds the request string: request line, `Host`, `Authorization: Bearer <ApiKey>`, `Accept: application/json`, `Connection: close`, blank line (`$R$N` = CRLF in ST strings) |
| NETIO_<hash> (`Runtime.IoCommon#I:=1;SD:STRING;RD:STRING`) | socket: INIT(QI, ENDPOINT) → INITO(QO); REQ sends SD; IND delivers RD/RD_LEN; ACK asks for the next chunk; parameter SNI = TLS server name |
| PackageHandler (basic) | accumulates chunks (≤ 4096 B), parses status line, headers, `Content-Length` or chunked encoding, then the JSON body into fixed arrays (3 rows × 5 regions); NEXT = more data, CNF = done, STOP |
| PackageFilter (basic) | keeps the newest complete rows, publishes one row per CNF (paced by E_DELAY T#1s) |
| E_CYCLE T#5m, E_PERMIT, E_DELAY | polling, "socket ready" gate, pacing |

## NETIO endpoint syntax (as used)

`'UDP:;8.8.8.8:53'`, `'TCP:;<ip>:<port>'`, `'TCPS:;<ip>:<port>'` (TLS; set `SNI` to the host name).
Exchange: `INIT` (QI=TRUE) opens → `INITO`/`QO`; `REQ` sends `SD`; each `IND` carries `RD`/`RD_LEN`; reply
`ACK` to receive the next part. With `Connection: close` the server ends the exchange.

## Limits and caveats of this pattern

- Sizes are fixed: request ≤ 512 chars, one chunk ≤ 1024 B, whole response ≤ 4096 B, body ≤ 2024 B;
  larger answers fail with status -4. Ask the API for small pages/fields only.
- JSON is parsed by hand for one response shape; a change in the API breaks it (consider the generic
  `JSON_PARSER` template: SET_PATH/PARSE → valueOut${CNT}).
- Never store an API key as an FB **parameter in plain text** in the `.fbt` (it ends up in every export/zip). Prefer a
  value injected at runtime (offline parameter / OPC UA / secured storage) and keep keys out of source control.
- DNS server and port are hard-coded (8.8.8.8:53); on plant networks use the site DNS or a fixed IP.
- Do date arithmetic on `DT` values (subtract `T#…` before `SPLIT_DT`) instead of by hand on hours/days.
- No retry/backoff beyond the 5-minute cycle; HTTP status ≥ 300 just yields no data.

## Making a new REST client with eae-mcp

1. `eae_http_probe url=… token=…` (enable `[http_probe]` in eae-mcp.toml for that host): checks status,
   size against the 4 KB buffer and lists JSON fields with the `path`/`occurrence`/`type` to extract.
2. `eae_rest_client_create name=… host=… path=… fields=[…] period=T#5m` (dry run first) generates:
   CAT `<name>` (INIT(QI, Token, Endpoint)/REQ → CNF(Status, fields) + IThis HMI interface, folder
   `.RestApi`), `<name>_Request`, `<name>_Response`, function `<name>_Json`, NETIO (TLS, SNI), E_CYCLE,
   E_PERMIT — the network above, with standard ECC branching instead of EventVariables.
3. Set `Token` at runtime (never in the type), `Endpoint` to `'TCPS:;<ip>:443'` if NETIO needs an IP, map
   the instance and build. Compile and run it once in EAE.

Extraction rule (`<name>_Json`): each dotted key is searched after the previous one; `occurrence` picks the
n-th match of the last key, so `price` + occurrence 2 reads the second `"price"` in the body. Strings are
returned without quotes (≤ 255 chars); numbers are converted with STRING_TO_REAL/DINT/INT.
