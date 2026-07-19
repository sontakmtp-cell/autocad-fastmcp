# Part-aware mechanical dimensioning

The annotation workflow supports both the original one-call path and an
approval-first path:

```text
annotation.detect_parts
annotation.plan_dimensions
annotation.commit_dimension_plan
annotation.auto_dimension
annotation.dimension_profiles
annotation.audit_dimensions
annotation.repair_dimension_layout
```

## 1. Select the intended part

`annotation.detect_parts` clusters touching/enclosed 2D geometry into stable
`part_1`, `part_2`, ... identifiers and attaches an indexed PNG preview. Parts
are ordered left-to-right and then bottom-to-top.

The planning and one-call tools accept exactly one selector:

```json
{"target_part_id":"part_3"}
```

```json
{"region":[0,0,120,80],"region_mode":"intersect"}
```

```json
{"entity_ids":["2F","30","31"]}
```

```json
{"selection":"current"}
```

`region_mode` can be `intersect` or `contained`. `source_layers` remains
available as a first-stage filter. Current-selection mode uses AutoCAD's
implied/PICKFIRST selection and is available through File IPC; the offline
ezdxf backend has no live editor selection.

Part detection uses 2D bounding-box connectivity. `part_gap_tolerance` can join
entities separated by a known small drafting gap. A block reference participates
as one bounded entity; its nested geometry is not exploded yet.

## 2. Preview, revise, then commit

`annotation.plan_dimensions` calculates dimension intent in memory and returns a
PNG with stable `D1`, `D2`, ... labels. It does not create, erase, move, or style
an AutoCAD entity.

```json
{
  "target_part_id":"part_3",
  "profile":"mechanical_mm",
  "mode":"balanced"
}
```

Revise the same draft by passing its `plan_id` and current revision:

```json
{
  "plan_id":"dplan_...",
  "expected_revision":1,
  "remove_dimension_ids":["D4"],
  "placement_overrides":{"D2":{"base":[0,95]}},
  "add_dimensions":[
    {
      "kind":"linear",
      "geometry":{"p1":[20,30],"p2":[80,30]},
      "placement":{"base":[20,90],"angle":0},
      "metadata":{"category":"requested_center_distance"}
    }
  ]
}
```

Commit only after approval:

```json
{"plan_id":"dplan_...","expected_revision":2}
```

Discard an unused draft with
`{"plan_id":"dplan_...","discard":true}` to release its in-process slot.

File IPC commits the complete plan through one dedicated safe command and one
AutoCAD `UNDO` group. A repeated commit of the same in-process plan is
idempotent. Draft plans are held in server memory, so a server restart requires
creating a new plan. If AutoCAD finishes a commit but the server process dies
before receiving its report, the plan has no persistent transaction marker and
must be audited before retrying. `annotation.auto_dimension` remains available
as the immediate plan-and-commit shortcut and accepts the same selectors/profile.

## 3. Mechanical feature intent

The deterministic geometry layer recognizes:

- repeated equal holes (`4x %%c<>` rather than four diameter dimensions);
- concentric circles/arcs;
- obround slots made from two semicircles and two connecting lines;
- repeated fillet radii;
- 45-degree polyline chamfers;
- symmetric equal-hole patterns and center distances.

Overall and feature coordinates use the profile's preferred layout. The
mechanical profiles default to baseline dimensions, avoiding chain accumulation.
`ordinate` is accepted in saved profiles but currently falls back to the same
baseline placement engine; native ordinate entities are not emitted yet.
AutoLISP/ezdxf calculates coordinates; the MCP-facing layer only selects which
recognized intent belongs in the plan.

## 4. Reusable profiles

Built-ins:

- `mechanical_mm`
- `mechanical_inch`
- `iso_simple`

Use `annotation.dimension_profiles` with `data.action` equal to `list`, `get`,
`save`, or `delete`. Custom profiles persist to
`%LOCALAPPDATA%\autocad-mcp\dimension_profiles.json` by default, or to the path
in `AUTOCAD_MCP_DIMENSION_PROFILES`.

A profile controls DIMSTYLE, layer, declared unit, precision, text/arrow size, row
spacing, scale, tolerance mode/values, diameter/radius/quantity notation,
centerlines, and baseline/ordinate/chain preference. A call can use temporary
`profile_overrides` without replacing the stored profile. The unit field records
the intended drafting standard; it does not rescale existing drawing geometry.

## 5. Audit and repair

`annotation.audit_dimensions` checks duplicate dimensions, wrong layer/style,
text and line overlap, dimension lines crossing geometry, missing overall/hole
location dimensions, detached geometry references, and displayed chain totals.
The attached preview uses green for valid dimensions, yellow for review, and red
for errors.

`annotation.repair_dimension_layout` applies only deterministic fixes: delete a
duplicate, set the expected layer/style, or move a crowded dimension to the next
lane. Pass the `audit_id` returned by the audit; repair refuses to run if the
drawing changed after that audit. File IPC applies the selected repair batch in
one Undo group. Missing
dimensions and detached references stay unresolved because recreating them
requires design intent and should go through a new preview plan.

## Common options

- `mode`: `minimal`, `balanced` (default), or `detailed`.
- `include_overall`, `include_features`, `include_holes`, `include_arcs`,
  `include_centers`, `detect_symmetry`.
- `clear_existing`: remove annotation on the chosen profile layer only for an
  unscoped whole-drawing run. It is rejected with part/region/entity selectors
  so dimensions belonging to another part cannot be erased accidentally.
- `spacing`: explicit positive row spacing.
- `source_layers`: geometry layers to inspect.
- `dimension_layer`: backward-compatible one-call override for the profile layer.

## Current boundaries

The workflow targets 2D LINE/POLYLINE/CIRCLE/ARC/ELLIPSE geometry. Block
references are selectable but nested feature recognition is not yet exploded.
Slot recognition expects two semicircles plus two lines; chamfer recognition is
currently limited to 45-degree polyline segments. Overlap/crossing checks are
geometric heuristics and should be visually approved on crowded assembly views.
Custom File IPC text must use ASCII plus AutoCAD `%%c`/`%%d` escape notation.
At most 128 plans are kept in one server process. Completed plans are evicted
before drafts; active drafts are never silently evicted and should be committed
or explicitly discarded.
