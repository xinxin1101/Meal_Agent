# Staging area

Only untrusted, source-attached recipe records belong here. A record must retain
capture time, raw content hash, licence status, original ingredient text,
normalisation warnings, and review status. `ingestion.publish()` is the only M6
code path that may write to a published dataset; it refuses pending/rejected
licences or reviews and duplicate recipe versions.

`raw/` is intentionally git-ignored and excluded from Docker build contexts.
MeishiChina personal-study batches retain ordered text instructions but never
download images and always start as untrusted, pending, and non-publishable.
