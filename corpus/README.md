# Corpus

The runbooks in `runbooks/` are synthetic. They were written for this project to describe a fictional streaming data platform (consumers, pipelines, sinks, brokers) and the four actions fleet-api exposes: `restart_consumer`, `scale_consumer`, `pause_pipeline`, `reset_consumer_offset`.

They are not taken from any real organisation's documentation and must not be used as operational guidance for real systems.

Each runbook is split into chunks at `##` headings. Chunk ids are `<file stem>#<heading slug>` and stay stable as long as the file name and heading do not change.
