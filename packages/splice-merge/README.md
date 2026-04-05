# Splice Merge

The purpose of this package is to support 3-way merge of nontrivial data formats.
It is not a complete merge solution for any particular format.

In particular Splice Merge is intended to work with a few patterns:

1. Objects whose identity cannot be 100% reliably determined based on its contents should have a statistically unique ID.
   - For example, think two identical events added concurrently to a calendar by two different people.
   - Recommended: UUIDv7 or similar.
2. Rather than merging raw stored data, prefer an export/canonicalize, merge, import cycle.
   - Canonicalizing data makes it easier for simple merge algorithms to perform well.
   - Splice Merge is not designed for low-latency use cases such as real-time collaborative editing. It is for cases where it is okay to trade speed for accuracy and reliability.
3. For files that might be large, prefer scanning versions simultaneously
   - Handle merging of objects independently
4. For 'large' objects, add a hash of its contents for quick comparison.
