# Confluence whiteboard data model

Reference for what a Confluence Cloud whiteboard can contain and how it is
stored, as used by `wb2canvas`.

## Provenance

Extracted in October 2026 from the canvas editor's JavaScript as served to a
live Confluence Cloud site. It was read-only: the scripts the page had
already loaded were searched in place. The relevant bundles were:

| Bundle (hash varies per deploy) | Contains |
|---|---|
| `wdf-models-*.js` | Element-type registry, Yjs model classes |
| `properties-*.js` | Short-key ↔ property-name dictionary |
| `a1b2c3-*.js` | Shape enum, shape definitions, localized shape names |
| `svg-transformer-*.js` | Atlassian's own SVG exporter, and the curve path finder |
| `slides-*.js` | A second copy of the editor engine: connector router, arrowheads |
| `agent-*.js`, `create-issue-command-*.js` | Rovo AI integration (SVG ↔ canvas) |

`slides-*.js` and many other chunks load lazily and are missing from the
page's resource list; they are reachable through the asset URLs listed in
the main bundle. The geometry findings below were checked against the live
board: element boxes land within 2–5 px of a screenshot.

Names come from compiled TypeScript enums (`e[e.Database=13]="Database"`)
and from i18n message catalogs. The **numbers** are persisted in every board
and should stay stable; the bundle file names change with every deploy, so
re-deriving this means finding the code by content, not by name.

Some type enums are compiled as `const enum`, which inlines the numbers and
deletes the names; those were recovered from the places that consume them.

## Storage

The board is a Yjs document. Its top-level shared types are `board` (a map
of element id → element map), `dimensions`, `zindex` (draw order), `paths`,
`lock`, `author`, `trees`, `layer`, `slide`, `collaborationTools`,
`groupIdByElementId`, `votes` and `meta` (`version`).

`dimensions` holds geometry as `{key: "<prefix>#<elementId>", val}` entries:
`p` position, `s` size, `bp` basis position, `bs` basis size and `fs` font
scale. (`fs` is font scale only in this array; as an element property it
means file status. See the dictionary.)

The **clipboard** payload (`data-canvas-clipboard`, base64 JSON) uses the
long property names from the dictionary below.

### Where elements are drawn

**`position` is the centre** of the box an element is drawn in, in the
clipboard, in `dimensions` and in Atlassian's exporter alike.

Shapes, text and stickies also carry a *basis* box (`basisPosition`, again a
centre, and `basisSize`): the box before its content grew it. Growth keeps
one corner fixed, so the drawn box is:

```
size   = basisSize + 2 × |position − basisPosition|
corner = position − size / 2
```

A shape whose text needs more room grows downward, its top edge fixed.
"Sources" on a sample board: basis height 110.3, centre 39.1
below the basis centre, so it is drawn 188.5 high. Left-aligned free text
grows rightward from its left edge, so a column of labels shares
`basisPosition.x − basisSize.x / 2`.

The stored `size` of these elements is stale: every shape on a sample board has 160×160. Images, by contrast, are drawn at `position`/`size`,
centred. Freehand `path` elements store absolute `start`/`end` points.

A connector's clipboard `start`/`end` are computed from that stale `size`
(centre ± 80), so they are only useful for an end attached to nothing.
Its `segments` are cached curve data that no longer match the board.

Each element type declares which box it uses
(`positioning: {basisSizing, basisPositioning, fontScaling}`):

| Basis box (and font-scaled) | `position`/`size` |
|---|---|
| `shape`, `text`, `sticky` | `section`, `image`, `connector`, `line`, `path`, `pathLabel`, `stamp`, `sticker`, `smartLink`, `issue`, `smartConnector`, timers, `flags` |

`pathWaypoint` uses basis position only. The flags of the remaining types
(card, table, mind map, …) were not found.

## Element types

| Type id | Model class | What it is | wb2canvas |
|---|---|---|---|
| `shape` | YShape | Shape with text; kind in `shape` (below) | ✅ |
| `text` | YText | Free-floating text | ✅ |
| `connector` | YConnector | Line between two elements | ✅ |
| `image` | YImage | Uploaded image (Atlassian Media) | ✅ |
| `path` | YPath | Freehand line, e.g. a divider | ✅ (straight segment) |
| `pathWaypoint` | YPathWaypoint | Bend point of a connector | ✅ (bends its connector) |
| `pathLabel` | YPathLabel | Label on a connector | ❌ |
| `sticky` | YSticky | Sticky note | ❌ |
| `section` | YSection | Titled frame grouping elements | ❌ |
| `group` | YGroup | Grouping without a frame | ❌ |
| `table`, `table-cell` | YTable, YTableCell | Table | ❌ |
| `mindmap` | YMindMap | Mind map tree | ❌ |
| `card` | YCard | Card | ❌ |
| `issue` | YIssue | Jira issue card | ❌ |
| `smartLink` | YSmartLink | Link card (unfurled URL) | ❌ |
| `smartConnector` | YSmartConnector | Jira issue relationship line | ❌ |
| `stamp`, `sticker` | YStamp, YSticker | Stamps / reactions | ❌ |
| `icon` | YIcon | Icon from an icon collection | ❌ |
| `drawing` | YDrawing | Pen drawing | ❌ |
| `composite-shape` | YCompositeShape | Multi-part shape (UML class, …) | ❌ |
| `line` | YLine | Line | ❌ |
| `comment` | YComment | Comment pin | ❌ |
| slide types | YSlide, YSlideChildElement, YSlideDecorationElement, YSlideChartElement | Presentation mode | ❌ |
| `timer`, `timerV2`, voting session, `flags` | YTimer, YVotingSession, … | Collaboration tools | ❌ |

Elements marked ❌ are omitted from exports, and `wb2canvas` logs a warning
counting how many of each were omitted, so nothing disappears silently. For
most of them the clipboard field layout has not been observed yet; supporting
one needs a sample board containing it.

A waypoint names its connector by `sourcePathIndex` (the connector's index in
the clipboard array; in the Yjs document, `pi` is the connector's id). `order`
sorts a connector's waypoints, and `axis` constrains how its handle can be
dragged.

## Property dictionary

The Yjs document stores elements under short keys; the clipboard uses the
long names.

**Keys are reused** across element types (e.g. `st` is a connector's
`stroke` weight but also `start`; `e` is `embedHeight` and `end`; `r` is
`resolved` and `rotation`; `f` is `followUps` and `fontScale`). Interpret a
key only together with the element's `t` (type).

| Name | Key | Name | Key | Name | Key |
|---|---|---|---|---|---|
| id | `id` | type | `t` | position | `p` |
| size | `s` | nativeSize | `ns` | color | `c` |
| text | `tx` | direction | `dr` | url | `u` |
| kind | `k` | mimeType | `mt` | supported | `su` |
| supportsPreview | `sp` | ari | `ari` | embedHeight | `e` |
| fileId | `fi` | fillEnabled | `fe` | fileStatus | `fs` |
| allowFlexibleWidth | `fw` | spriteId | `si` | shape | `sh` |
| sourcePathId | `pi` | sourceElement | `se` | sourceAnchor | `sa` |
| targetElement | `te` | targetAnchor | `ta` | presentation | `pr` |
| startCap | `sc` | endCap | `ec` | stroke | `st` |
| strokeColor | `stc` | strokeStyle | `sts` | title | `ti` |
| titleWidth | `tw` | sectionConfig | `cf` | relationshipId | `ri` |
| relationDirection | `rd` | startTimeUTCMilliSeconds | `sm` | durationMilliSeconds | `du` |
| elapsedTimeMilliSeconds | `el` | paused | `pa` | dropShadow | `ds` |
| alignment | `a` | verticalAlignment | `va` | commentId | `ci` |
| version | `v` | createdBy | `cb` | authorIds | `aids` |
| resolved | `r` | attachedTo | `at` | imageHash | `ih` |
| isTransparent | `it` | isPlaceholder | `ip` | axis | `ax` |
| order | `or` | iconId | `iid` | category | `ica` |
| collection | `ico` | maxVotesPerUser | `mv` | isAnonymous | `ian` |
| isVotingAnonymous | `iv` | isSessionActive | `ia` | followUps | `f` |
| generatedFromPrompt | `gp` | generatedFromElement | `ge` | content | `co` |
| children | `ch` | rootId | `rid` | treeOrientation | `to` |
| points | `pt` | source | `so` | enableResizeAndDrag | `er` |
| unfurledConnections | `uc` | unfurlState | `us` | compositeRootId | `cr` |
| deckId | `dki` | slideIndex | `si2` | role | `rl` |
| singleLine | `sl` | backgroundColor | `bc` | groupLayoutConfig | `glc` |
| layoutConfig | `lc` | decorationId | `did` | decorationVariant | `dvr` |
| isSlice | `isl` | tertiaryBackground | `tbg` | groupId | `gid` |
| rows | `rw` | cols | `cl` | hasHeaderRow | `thr` |
| hasHeaderColumn | `thc` | hasNumberedRow | `tnr` | rowOrder | `tro` |
| colOrder | `tco` | rowDefs | `trd` | colDefs | `tcd` |
| tableId | `tid` | rowId | `tr` | colId | `tc` |
| gridRow | `gr` | gridCol | `gc` | tableCellMerge | `tcm` |
| chartType | `cty` | chartVersion | `cvr` | chartPayload | `cpl` |
| basisPosition | `bp` | basisSize | `bs` | fontScale | `f` |
| start | `st` | end | `e` | rotation | `r` |

Colours (`c`, `stc`) are stored as 12 bytes: three big-endian float32 RGB
channels (0–255).

## Shape kinds (`shape` / `sh`)

`0` is the model's default and is not offered in the toolbar. Kinds 82–83
are *composite* shapes (several parts). When Atlassian's own exporter meets
a kind it cannot draw, it falls back to `rectangle`.

The **wb2canvas** column is the stereotype the SVG and HTML outputs draw
(`rect` is sharp-cornered). A `*` means approximate. Kinds marked — have no
stereotype: they are drawn as a sharp rectangle, as Atlassian's exporter does,
and a warning names them. JSON Canvas has no shapes, so every kind becomes a
card there.

| # | Key | Name | wb2canvas |
|---|---|---|---|
| 0 | sharp-rectangle | Sharp rectangle (default; not in the picker) | rect |
| 1 | rectangle | Rectangle | rect |
| 2 | ellipse | Ellipse | ellipse |
| 3 | rounded-rectangle | Rounded rectangle | rounded-rect |
| 4 | diamond | Diamond | diamond |
| 5 | triangle | Triangle (point up) | triangle |
| 6 | upside-down-triangle | Upside down triangle | down-triangle |
| 7 | left-parallelogram | Left parallelogram | left-parallelogram |
| 8 | right-parallelogram | Right parallelogram | right-parallelogram |
| 9 | start-end | Start or end | stadium |
| 10 | document | Document | document |
| 11 | off-page | Off-page connector | off-page |
| 12 | input-output | Input or output | right-parallelogram |
| 13 | database | Database | cylinder |
| 14 | sum | Sum (summing junction) | circle-cross |
| 15 | or | Or | circle-plus |
| 16 | predefined-process | Predefined process | predefined-process |
| 17 | internal-storage | Internal storage | internal-storage |
| 18 | manual-input | Manual input | manual-input |
| 19 | manual-operation | Manual operation | manual-operation |
| 20 | multiple-documents | Multiple documents | document* |
| 21 | preparation | Preparation | hexagon |
| 22 | hard-disk | Hard disk | hard-disk |
| 23 | comment-left | Comment left | comment-left |
| 24 | comment-right | Comment right | comment-right |
| 25 | stored-data | Stored data | stored-data |
| 26 | delay | Delay | delay |
| 27 | display | Display | display |
| 28 | process | Process | rect |
| 29 | decision | Decision | diamond |
| 30 | connector | Connector | ellipse |
| 31 | merge | Merge | down-triangle |
| 32 | cloud | Cloud | cloud |
| 33–51 | key, server, archive, browser, user, compute, computer, file, firewall, folder, frontend, internet, lock, mail, mobile, settings, shield, users, switch | Architecture icons | — |
| 52 | database-advanced | Database (advanced; key derived from the enum name) | cylinder* |
| 53 | alternate-process | Alternate process | rounded-rect |
| 54 | use-case | Use case | ellipse |
| 55 | classifier | Classifier | — |
| 56 | note | Note | note |
| 57 | interface-2 | Simple interface | — |
| 58 | activation | Activation | — |
| 59 | activity | Activity | rounded-rect |
| 60 | actor | Actor | — |
| 61 | assembly | Assembly | — |
| 62 | component | Component | — |
| 63 | deletion | Deletion | — |
| 64 | end | End | ellipse* |
| 65 | flow-final | Flow final | circle-cross |
| 66 | gateway | Gateway | diamond |
| 67 | history-pseudostate | History pseudostate | ellipse* |
| 68 | horizontal-fork | Horizontal fork | — |
| 69 | vertical-fork | Vertical fork | — |
| 70 | off-page-link | Off-page link | off-page |
| 71–75 | pin-filled-left, pin-filled-right, pin-left, pin-right, pin | Pins | — |
| 76 | provided-interface | Provided interface | — |
| 77 | receive-signal | Receive signal | — |
| 78 | required-interface | Required interface | — |
| 79 | send-signal | Send signal | — |
| 80 | start | Start | ellipse* |
| 81 | template | Template | — |
| 82 | class | Class (composite) | — |
| 83 | interface | Interface (composite) | — |
| 84 | node | Node | — |
| 85 | container | Container | — |
| 86 | boundary-object | Boundary object | — |
| 87 | entity-object | Entity object | — |
| 88 | control-object | Control object | — |

Use `--shape-map` to change a mapping, by number or by key, e.g.
`--shape-map 'database=hard-disk,60=ellipse'`. The stereotype names are
listed in `convert --help`.

## Connector and line enums

| Property (key) | Values |
|---|---|
| presentation (`pr`), routing | 1 straight · 2 dynamic (right-angled) · 3 curved |
| startCap / endCap (`sc`/`ec`) | 1 none · 2 arrow · 3 filled-arrow · 4 open-arrow · 5 filled-diamond · 6 open-diamond · 7 open-circle · 8 slash · 9 triple-bar · 10 open-circle-cross · 11 cross · 12 cross-crows-foot · 13 crows-foot · 14 circle-crows-foot |
| strokeStyle (`sts`) | 0 none · 1 solid · 2 dashed · 3 dotted |
| stroke (`st`, weight) | 1 small · 2 medium · 3 large |
| axis (`ax`, waypoint) | 0 x · 1 y · 2 none |
| relationshipId (`ri`), Jira | 1 issue hierarchy · 2 issue link |
| relationDirection (`rd`) | 1 towards source · 2 towards target |

### Connector routing

The editor routes connectors itself; nothing about the drawn path is stored
except the waypoints. `wb2canvas` reimplements its router:

- **Ends.** Each end sits on its element's drawn box at the anchor
  (`left`/`top` fractions). Its direction is the box side the anchor is on,
  chosen by the anchor's dominant axis, ties going horizontal. An anchor at
  the centre, or an end attached to nothing, has no direction.
- **Curved** connectors are a "smooth series" spline through
  `[start, waypoints…, end]`:
  - Each waypoint's tangent bisects the directions to its neighbours.
  - Handles are `0.38 ×` the distance to the neighbour; the factor was 0.3
    before the editor's `contentWrapperMainToolbarRefresh` flag, now always on.
  - End handles follow the end directions.
  - An end with no direction on a two-point curve leaves at angle
    `w + 0.68 · sin(2w − π)`, where `w` is the start-to-end angle.
- **Straight** connectors are a polyline through the waypoints.
- **Dynamic** connectors are right-angled and avoid other shapes.
  `wb2canvas` uses a simpler route that leaves and enters perpendicular to
  the box edges, and takes a staircase through waypoints.

Line thickness is 2, 4 or 6 board units for stroke sizes 1–3.

### Line ends

The editor's arrowhead table, with the `diagrammingArrowheads` experiment
on (as on the site examined; with it off, caps are a plain on/off arrow):

| Cap | Look | Size | Offset | Line under it |
|---|---|---|---|---|
| arrow | open chevron `>` | 10×14 | 0 | visible |
| filled-arrow | filled triangle | 8×12 | 4 | hidden |
| open-arrow | outlined triangle | 11×15 | 5 | hidden |
| filled-diamond | filled diamond | 16×12 | 8 | hidden |
| open-diamond | outlined diamond | 17×13 | 8 | hidden |
| open-circle | circle | 9×9 | 4 | hidden |
| slash | diagonal stroke | 8×12 | 4 | visible |
| triple-bar | three bars on the line | 14×14 | 6 | hidden |
| open-circle-cross | circle, then a bar at the end | 14×14 | 10 | hidden |
| cross | one bar across the line | 12×12 | 6 | visible |
| cross-crows-foot | bar, then crow's foot | 12×12 | 6 | hidden |
| crows-foot | crow's foot | 12×12 | 6 | hidden |
| circle-crows-foot | circle, then crow's foot | 24×12 | 12 | hidden |

The graphic is drawn in a unit box (x 0..1 toward the end, y −0.5..0.5),
scaled to its size times 1, 1.2 or 1.6 for stroke sizes 1–3. Its centre
sits `offset` before the end, so the tip lands on the box edge. The router
extends the path by `2 × offset` (scaled) along the end direction, as a
straight stub. Where the line is "hidden", that stub is not drawn and the
graphic covers it.

Atlassian's own SVG exporter ignores all of this: it draws only `arrow`, as
a filled triangle. JSON Canvas has only `arrow` and `none`, so `wb2canvas`
maps the three arrowheads to `arrow` and drops the others with a warning.
JSON Canvas edges are drawn by the viewer (Obsidian curves them), so routing
and waypoints are lost there too.

## Text enums

| Property (key) | Values |
|---|---|
| alignment (`a`) | 0 centre · 1 left · 2 right (clipboard: `"center"`, `"left"`, `"right"`) |
| verticalAlignment (`va`) | 0 top · 1 middle · 2 bottom |
| text style | −1 mixed · 0 normal · 1–6 heading 1–6 · 7 impact heading |
| direction (`dr`), mind maps | 0 horizontal · 1 vertical · 2 horizontal reverse · 3 vertical reverse |

## Other enums

- **fileStatus (`fs`)**: 0 pending · 1 uploading · 2 downloading · 3 upload error ·
  4 download error · 5 ready · 6 local only.

## Feature flags that change geometry

| Flag | Source | Effect | Observed |
|---|---|---|---|
| `contentWrapperMainToolbarRefresh` | compiled in | curve tension 0.38 instead of 0.3 | on |
| `diagrammingArrowheads` | experiment | the arrowhead table above | on |
| `cc-whiteboards-copy-paste-basis-position-origin` | Statsig | paste origin from drawn positions (does not change element fields) | on |

## Geometry used by Atlassian's SVG exporter

Exporter positions are element centres; formulas here are restated for a
top-left box `(x, y, w, h)`.

- **Rounded rectangle:** corner radius `0.1 × w`.
- **Triangle:** apex at top centre; the upside-down variant has its apex at bottom centre.
- **Diamond:** the four edge midpoints.
- **Parallelograms:** the top edge spans the box. The bottom edge is shifted
  by `k × h` with `k = −0.2` (right, leaning `/`) or `+0.2` (left, leaning
  `\`), so the shape extends past its box by 20% of its height.
- **Arrowhead:** filled triangle of size `max(3 × stroke width, 6)`.
- **Dash patterns:** dashed `12`, dotted `4`. The editor's own shape preview
  dashes at `28, 16`; the live board's dashes look about as long as that
  ratio suggests, and longer than the dashes `wb2canvas` draws (scaled to the
  stroke width). Dotted lines are drawn as round dots.
- **Routing:** curved connectors pass through the waypoints with no end
  directions, dynamic ones are an orthogonal polyline, straight ones a line.
