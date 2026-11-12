# maestro

A Claude Code harness that builds a complete, sourced canon of one specified *maestro*: a singer, composer, lyricist or other contributor to music, from any country or genre.

Pilot maestros: Mohammed Rafi, Eddy Arnold and Jean Wiener.

The harness is a generic core driven by a per-maestro *pack* (`maestros/<slug>/maestro.yaml`). A profiler researches the maestro and proposes the pack; an orchestrator then runs a crawl, resolve and measure loop until each canon tier meets its completeness target.

Status: Phase 0 (foundations). See the design doc for the full architecture.
