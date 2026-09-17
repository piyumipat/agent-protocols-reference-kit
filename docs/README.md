# Protocol docs

One `<protocol>.md` file per implemented protocol, each following the same structure:

1. **Overview** — one-paragraph plain-language summary of what the protocol does and where it fits.
2. **Typical use cases** — practical situations where the protocol is useful.
3. **Core capabilities** — functionality defined by the protocol, independent of this kit's
   current coverage.
4. **Lifecycle** — a diagram of the protocol's normal end-to-end interaction, its principal
   participants, and request/response directions. Reusable visual assets live in `docs/assets/`.
5. **Security** — the protocol's security model, trust boundaries, and safeguards.
6. **Specification** — link(s) to the authoritative spec/standard this implementation targets,
   and the exact version/revision being followed.
7. **Implementation coverage in this kit** — implemented and missing protocol capabilities and
   security mechanisms.
8. **Module map** — which files under `src/agent_protocols/<protocol>/` implement which concept.
9. **Usage** — a numbered, step-by-step guide to actually running the protocol's example end to
   end (see [`examples/`](../examples)), and an isolated code snippet.
10. **Known limitations / deviations** — anything this implementation does differently from, or
   doesn't yet cover in, the spec.

Installation is covered once in the top-level [`README`](../README.md), not repeated here. These
are implementation guides for *this repo* specifically, not protocol background/survey material.

- [`mcp.md`](mcp.md)
- [`a2a.md`](a2a.md)
- [`anp.md`](anp.md)
- [`agora.md`](agora.md)
