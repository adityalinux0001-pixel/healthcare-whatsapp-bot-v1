# Built knowledge base

The runtime knowledge base is a **grounded evidence layer**, not a general-purpose document dump.

Build it with:

```bash
python scripts/build_knowledge_base.py --replace
```

The default build indexes only authoritative sources. Use `--include-supporting` only when non-clinical example datasets are intentionally needed.

Runtime retrieval applies:

- source-role filtering;
- minimum semantic-similarity thresholds;
- an authority-weighted score;
- a minimum authoritative-result requirement for plan generation;
- provenance metadata (`source`, `reference`, and URL).

The application never falls back to free-generated health guidance when authoritative context is unavailable.
