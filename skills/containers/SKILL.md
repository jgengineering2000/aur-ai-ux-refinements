---
name: containers
description: Read BEFORE designing or changing any reader, parser, decoder, dictionary, schema or table of fields for nested data (file formats, metadata, archives, protocols, filesystems, JSON/XML, anything with a header that describes what follows) — and whenever a table you are writing has blank cells, a column that applies to only some rows, or a column that names a property of something other than the row. The owner's Data/Container model, its math, the normalisation it implies, and the checks that catch a denormalised design before it is built. Pair with the `thinking` skill.
---

# Containers — everything is Data, and a Container is Data that holds Data

## The owner's statement (2026-10-24)

> Everything on a computer is essentially either Data or Container (which is a specialized type of Data) and
> Containers contain Data (and possibly more Containers) recursively ad infinitum.
>
> Every Data and every Container has (in essence) a type. Sometimes more general, and sometimes more specific, that
> allows us to know what type of data are contained within, defined by some sort of dictionary (the specs). JSON, for
> example, somewhat combines the two and XML has them in two separate formats.
>
> Sometimes the Data (and Containers) can exist in more than one type of container. For example a Directory (type of
> container) can be inside of a tar which can be inside of a compression file (gzip) which is in turn in a File System
> Directory which is inside a File System which is inside a Partition which is inside a Drive, which is inside a Host.
>
> When I talk about mathematically pure this is what I mean. It is recursively consistent and normalized or it is
> buggy, and your empty fields are literally denormalization by accident (as opposed to an intentional cache/hint —
> which can be a valid denormalization, but requires explicit management and complexity).
>
> Every level is (in essence) fields that provide state that applies to sub fields (when there are sub fields).
>
> We know, therefore, that there are types of data formats (little endian two's complement integers for example)
> within field formats (time format), or descriptions as a variable length UTF-8 string of text. This can not be half
> assed by looking at it as if the end point data exists without context when we are literally working with a nested
> hierarchy.

And the same day, on what the table is:

> The data type u16 can contain a time or a counter or the number of days since an epoch. Same with UTF-8 or
> ASCII. The point being that the table per se is a dictionary of field types, and is the fastest and cleanest
> model. Your algorithm processes recursively, but JSON is the lazy way to commingle the data, dictionary, and
> relationship hierarchy instead of making your conceptual algorithm the driver that leverages the field
> dictionary/spec to express the model (which is in essence mathematical).

## The math, in its final form

```
Σ  states: name -> value (byte order, encoding, base), set by fields, never stored in rows
R  representations  ρ : bytes × Σ -> V     u8 u16 u32 s32 rational f32 ascii utf8 utf16 bytes (width is ρ's)
I  interpretations  ι : V -> M             count, time of day, date-time, days since epoch, enum E, version,
                                           ratio, and state setters (ι maps "II" to little-endian into Σ)
C  container types, each with one framing  f_c : bytes × Σ -> [(k, s_k)]

D : C × K -> (R ∪ C) × I × Name            THE dictionary: one flat row per (container type, key)

read(c, s, σ):
    for (k, s_k) in f_c(s, σ):
        (τ, ι, name) = D(c, k)                       a miss: (bytes, raw, unknown), still emitted
        if τ ∈ C:  read(τ, s_k, σ)                    the hierarchy exists only here
        else:      m = ι(τ(s_k, σ)); emit(path·k, name, m)
                   if ι sets state: σ = σ ⊕ m        scoped to later siblings and their descendants
```

- **Representation and interpretation are orthogonal.** |R| + |I| functions give |R| × |I| field types: one u16
  is a counter, a time or a day count; one time is ASCII, three rationals or a u32. Neither axis is a row column
  repeated per field; each is a named function.
- **The table is flat at every depth.** Nesting is the fixed point of `read`, never a nested data structure.
  JSON (or any nested document) that stores the hierarchy stores what the algorithm computes, and commingles
  data, dictionary and relationship: the defect, not a format preference.
- **Three tables, nothing else:** fields D (container type, key, representation, interpretation, name,
  description), containers (container type, framing), enumerations (enumeration, value, meaning). Code: the
  framings, the representations and the interpretations, each a map name -> function, plus `read`.
- **Normal form by construction:** every column of D depends on (c, k), the whole key and nothing but the key.
  Width belongs to ρ, byte order to σ, framing to c; none is a column of D.
- **Pointers stay in the fold:** a pointer is a representation (an offset) with the interpretation "locate
  container c′", which calls `read(c′, span at base + v, σ)`.
- **Self-description is a constraint:** where the file carries a child's type (a TIFF entry's type code), the
  file picks ρ and D's ρ checks it.

## The model, in prose

**Node.** Every thing read is a node `n = (τ, s, σ)`: a type `τ`, a span `s` of the parent's bytes, and the state `σ`
in force where it sits. There are two kinds of type:

- **Data type**: `decode_τ(s, σ) → value`. It is a leaf only relative to the depth we choose to read: a RATIONAL
  is a container of two integers, a time text is a container of characters, an integer is a container of bits whose
  order and sign rule come from `σ`. The recursion stops at primitive types the machine decodes.
- **Container type**: `frame_τ(s, σ) → [(k₁, s₁), (k₂, s₂), …]`, the ordered children, each with its **key** (a tag,
  a marker, a position, a name) and its span. How children are delimited (fixed sequence, count then entries,
  marker-and-length run, tag-length-value, offset pointers) is the container type's **framing**; framing kinds are
  few and each is one function.

**Containment** is a relation, defined once per container type by its spec (its dictionary):

```
child_type(τ, k [, discriminator]) → τ_k
```

**State flows down.** Some children of a container are fields whose value the spec says governs everything beneath
(byte order, text encoding, offset base, version). The state a child sees is its parent's state extended by those
fields:

```
σ_k = σ ⊕ { name ↦ value(child j) : (τ, j, name) ∈ state_from }
```

**One recursive function reads everything:**

```
read(τ, s, σ) =
    decode_τ(s, σ)                                                   if τ is data
    [ read(child_type(τ, k), s_k, σ ⊕ δ_τ) for (k, s_k) in frame_τ(s, σ) ]   if τ is a container
```

A location is the path of keys from the root: `host / drive / partition / fs / dir / x.tar.gz / gzip / tar / dir /
x.jpg / APP1 / Exif / IFD0 / 0x0110`. The same path form serves every depth.

**Specificity.** A general type becomes a specific one by a discriminator the spec names (APP1 + "Exif\0\0" →
Exif; APP2 + "ICC_PROFILE\0" → ICC). That is still `child_type`, keyed by the discriminator; it is never a column.

**Placement independence.** A type's definition does not depend on what contains it: TIFF is TIFF in an APP1, in a
.tif file, or in a raw file. So a type is defined once, and a parent's dictionary only *references* it.

**References are not containment.** Some formats place a child elsewhere and point to it (a TIFF entry's
offset, a next-directory link, a box's offset table). The pointer is Data in the parent; the child it locates is
still the parent's logical child and still reads under the parent's state, but its span is not inside the
parent's. So containment is a tree of logical children over a graph of byte spans: spans may be shared or
overlap, and a pointer may loop. Two invariants follow: every logical child is reached once (a guard against
cycles and depth), and every byte of the parent is accounted for by exactly one child, a stated gap, or a stated
overlap.

**Self-description and the dictionary can disagree.** Some formats carry a child's type inside the data (a TIFF
entry's type code and count, an XMP element's name). That is a discriminator the file supplies; the dictionary's
expected type is a constraint on it. A disagreement is reported on that node and the file's own description is
read, never silently overridden.

**State must precede what it governs.** A field that sets state is read before the children it governs (or its
container's framing locates it independently of them). A design where a value's meaning depends on a field read
later, beside it or below it, has an unmodelled container or a misplaced state source.

## The computer science it maps to

The model is standard; naming the theory lets a design be checked against it rather than against intuition.

- **Algebraic data types.** A container with a fixed sequence of fields is a *product* type; a child whose
  specific type is chosen by a discriminator is a *sum* (tagged union); a count-then-entries or marker run is a
  *list*. A dictionary is the definition of those types.
- **Attribute grammars** (Knuth, 1968). State that flows from a parent to its children is an *inherited*
  attribute; a value computed from children and passed up (a length, a checksum, a coverage total) is a
  *synthesised* one. "State flows down from fields" is the inherited-attribute rule.
- **Structural recursion.** `read` is a fold over the type definitions: one case per kind (data, each framing
  kind), never one per type or per field. Code per framing kind and per capability, tables for everything else.
- **Relational normalisation** (Codd). A row describes one entity; a nullable column is a subtype or a different
  entity sharing the relation; repeated clusters of cells are an unnamed relation. Normal form is what makes "one
  fact, one place" checkable.
- **Declarative format languages** are existence proofs that whole binary formats can be described this way:
  ASN.1, DFDL (the Open Grid Forum's Data Format Description Language), Kaitai Struct. They are references for
  the shape of a description, never dependencies or sources of wording.

## What normalisation means here

- **One fact, one place: the level whose spec defines it.** A container type's own properties (its framing, which
  child sets its byte order, where its first directory starts) belong to that type's definition, never to the parent
  row that points at it.
- **The dictionary is D and its two side tables** (fields, containers, enumerations; see the math). State
  sources are not a table: a field that sets state does so by its interpretation. A property that applies only
  to a subtype lives with that subtype (framing with container types), never as a nullable column of D.
- **A blank cell is the signature of the defect.** It means the row holds two kinds of entity, or the column is a
  property of something other than the row. Either split the relation or move the column to the entity it describes.
- **A deliberate denormalisation is a cache:** named as one, derived from the normal form by a stated rule, and
  invalidated when its source changes. Anything else is an accident.

## The checks — run them on every table before it is built

1. **Blank-cell test.** Any column empty in some rows? Name the entity the column describes; it is not the row's.
2. **Rows-into-columns test.** Does a column name a child, a field or a property of a nested level (a byte order, a
   first directory, a "points to")? That is a containment row flattened into a column.
3. **Placement test.** If this type appeared inside a different container, would any of its row's cells change? The
   ones that would belong to the containment relation; the ones that would not belong to the type.
4. **State-source test.** For each piece of state (order, encoding, base), name the field at the level that sets it.
   A default in code, or a per-row column repeating it, is the defect.
5. **Repetition test.** The same cluster of cells repeated across rows (`1 × SHORT, enum:orientation`) is a type
   that was never named. Name it once; rows reference it.
6. **Depth test.** Can every value be explained as `read(τ, s, σ)` all the way down to a primitive, with `σ` coming
   only from ancestors? A value that needs knowledge from beside or below its path has an unmodelled container.

## Use with the `thinking` skill

Before proposing a schema or a reader, write the chain: what each table row *is* (its entity), which spec defines
it, which level owns each column, where each piece of state is set. Audit each node against the checks above; a
refuted node rewinds the design, never patches the table.

## Paid for

2026-10-24, metadata dictionary prototype. A flat `segments.csv` carried `signature, dictionary, first_directory,
byte_order` as columns on JPEG marker rows, blank on 16 of 18. Chain: the columns were what the reader needed when
it reached APP1 (an access-pattern decomposition, ai_common P3), so the TIFF type's own properties were written onto
the parent's row, and the Exif format, a type contained in an APP1, was treated as an attribute of the segment. The
same defect sat in the Exif field table: `points_to` (blank on most rows) was a child-type reference flattened into a
column, and `length, encoding, shown_as` repeated per row were unnamed data types. The supervisor then proposed
building on the JSON tables as "closer to normal form"; the owner corrected it: JSON commingles data, dictionary
and hierarchy, the hierarchy is the algorithm's, and the table is a flat dictionary of field types whose type is
representation × interpretation.
