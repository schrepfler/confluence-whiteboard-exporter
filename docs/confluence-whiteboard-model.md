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
centre, and `basisSize`): the box before its content grew it. The editor
lays them out from the basis box and grows it to fit measured content;
`position` is the centre it writes back afterwards. Growth keeps one corner
fixed, so for free text the drawn box is:

```
size   = basisSize + 2 × |position − basisPosition|
corner = position − size / 2
```

The editor writes the grown box back (`position`) only when the text is
edited. Pasted elements, and boards made by tools, store no growth even
though the editor draws them grown.

A **shape** has a fixed width and grows only downward (its sizing strategy:
`withFlexibleWidth(false)`, `withVerticalGrowDirection("downward")`), so its
drawn box is the basis box made `2 × (position.y − basisPosition.y)` taller
when that is positive: a shape with basis height 110 whose centre sits 40
below the basis centre is drawn 190 high. A `position` shifted up or
sideways is stale and ignored (a container whose centre sits 20 above its
basis centre is drawn at its basis box), and so is any shift of a shape
with no text, which the editor never measures (`isContentEmpty`). Left-aligned free text grows
rightward from its left edge, so a column of labels shares
`basisPosition.x − basisSize.x / 2`.

### How text is laid out

- **Type.** Paragraphs are `11.6 / 0.75` ≈ 15.47 px on a 22 px line
  (`defaultTextSpacing`), in "Atlassian Sans"; headings h1–h6 are 27/32,
  23/27, 18/23, 16/23, 14/18 and 13/18 px. There is no space between
  paragraphs or round lists: a box is whole 22 px lines plus padding.
- **Content box.** A shape's text goes in a content box
  (`getConverterForShape`), then 12 units of padding on every side, scaled
  by the font scale. The rectangle and rounded rectangle lose their corner
  length, `144 × roundedness` (0.02 and 0.225), overall; the ellipse keeps
  `1/√2` of the box, the diamond ½, the triangles half the width and height
  shifted a quarter-height towards the base, the parallelograms half the
  width. Drawn shapes use their drawing's text area; the sharp rectangle,
  its whole box.

With these, every shape on the boards compared with the live editor holds
its text exactly, and the editor's line breaks are reproduced.

### Colours

Boards store colours as RGB values of the legacy Atlassian palette. The
editor looks each one up by palette name (`elementColorMap`) and paints it
with that name's design token in the current theme (`allColorMapTokenNames`),
so stored `#172B4D` (text, `color.text`) is drawn `#292A2E`, the grey fill
`#B3B9C4` (`n400`) `#B7B9BE`, and connector grey `#758195` (`n600`)
`#7D818A` in the 2025 light theme. `wb2canvas/palette.json` holds the
resolved table; `scripts/dump_palette.js` and `scripts/build_palette.py`
regenerate it from a live board.

The stored `size` of these elements is stale: every shape examined stores 160×160. Images, by contrast, are drawn at `position`/`size`,
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
| `pathLabel` | YPathLabel | Label on a connector | ✅ (on its connector) |
| `advanced-icon` | — | Library icon, e.g. AWS (`iconId`, `category`, `collection`) | ✅ (named placeholder) |
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
sorts a connector's waypoints. On a right-angled connector `axis` says which
segment a waypoint's handle pins: 0 a vertical segment at its x, 1 a
horizontal one at its y.

A label names its connector the same way. It sits at `proportion` of the
path's length, centred on the line (`pathOffsetPosition` 0) or beside it
(1 left, 2 right), on a background that hides the line, with 4 units of
padding round its text.

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
are *composite* shapes (several parts). JSON Canvas has no shapes, so every
kind becomes a card there.

### How the editor draws shapes

The editor ships a registry of shape definitions, one per kind
(`shapeDefinitions`). Each carries a vector **drawing**: fill and stroke
sections of lines and cubic curves. Its points are written as
`(x fraction, y fraction, x offset, y offset)` and land at
`box corner + fraction × box size + offset`. The outline stretches with the
box, while offsets keep their size: a rounded rectangle's corners stay 35.2
units whatever its width, and a database's lid stays 26 units. A drawing also
defines its **text area**, and some kinds take no text at all.

How a kind is drawn depends on its renderer:

| Renderer | Meaning |
|---|---|
| `sharpRectangle` | a plain rectangle (kind 0 only) |
| `ellipse`, `roundedPolygon`, `graphics` | the drawing, stretched to the box |
| `replicate` | another kind's drawing (`replicatedShape`), e.g. decision = diamond |

**Outlines** are always 3 units wide, whatever the shape's stroke size.
Shapes are solid or dashed (they have no dotted style). A dash pattern
repeats every 12 line widths, so 36 units. The editor lays it out per renderer:

| Kinds | Dash layout |
|---|---|
| rounded rectangle, ellipse | A whole number of periods, evenly round the outline. The pattern runs clockwise, from where the top-left corner starts (rounded rectangle) or from the rightmost point (ellipse). |
| the other rounded polygons | Each rounded corner sits inside one dash; each edge carries whole periods, stretched to fit. |
| drawn shapes | Each stretch between sharp corners carries whole periods, starting and ending mid-dash. A stretch shorter than a period is solid. |

On textured outlines a dash is 60% of the period, round ends included;
drawn shapes use a 50% dash plus round ends. On a dashed rounded rectangle
this places the dashes within about 2 units of the live board's.

Icons, the architecture set of the `advanced` category, are drawn at a fixed
aspect ratio at the top of their box, with the label below (`exteriorTextArea`).
Section colours are the shape's fill and line colours. A section may swap them:
the UML start node, for example, is a dot filled with the line colour.

`wb2canvas` draws every kind from these definitions, stored in
`wb2canvas/shape_data.json`. Icon artwork is Atlassian's and is not
included, so icons are drawn as placeholders with their label below, and a
warning names them. To refresh the data after an editor update:

1. Paste `scripts/dump_shapes.js` into the DevTools console of an open whiteboard.
   It reads the module the page has already loaded and copies a JSON dump.
2. Run `pbpaste | uv run scripts/build_shape_data.py`.

Atlassian's own SVG exporter does not use these drawings. It approximates a
few shapes (for example, rounded corners at 10% of the width) and falls back
to a rectangle for the rest.

| # | Key | Name | Category | Drawn as |
|---|---|---|---|---|
| 0 | sharp-rectangle | Sharp rectangle (default; not in the picker) | — | plain rectangle |
| 1 | rectangle | Rectangle | basic | own drawing |
| 2 | ellipse | Ellipse | basic | own drawing |
| 3 | rounded-rectangle | Rounded rectangle | basic | own drawing |
| 4 | diamond | Diamond | basic | own drawing |
| 5 | triangle | Triangle (point up) | basic | own drawing |
| 6 | upside-down-triangle | Upside down triangle | basic | own drawing |
| 7 | left-parallelogram | Left parallelogram | basic | own drawing |
| 8 | right-parallelogram | Right parallelogram | basic | own drawing |
| 9 | start-end | Start or end | flowchart | own drawing |
| 10 | document | Document | flowchart | own drawing |
| 11 | off-page | Off-page connector | flowchart | own drawing |
| 12 | input-output | Input or output | flowchart | own drawing |
| 13 | database | Database | flowchart | own drawing |
| 14 | sum | Sum (summing junction) | flowchart | own drawing (no text) |
| 15 | or | Or | flowchart | own drawing (no text) |
| 16 | predefined-process | Predefined process | flowchart | own drawing |
| 17 | internal-storage | Internal storage | flowchart | own drawing |
| 18 | manual-input | Manual input | flowchart | own drawing |
| 19 | manual-operation | Manual operation | flowchart | own drawing |
| 20 | multiple-documents | Multiple documents | flowchart | own drawing |
| 21 | preparation | Preparation | flowchart | own drawing |
| 22 | hard-disk | Hard disk | flowchart | own drawing |
| 23 | comment-left | Comment left | flowchart | own drawing |
| 24 | comment-right | Comment right | flowchart | own drawing |
| 25 | stored-data | Stored data | flowchart | own drawing |
| 26 | delay | Delay | flowchart | own drawing |
| 27 | display | Display | flowchart | own drawing |
| 28 | process | Process | flowchart | same as 1 |
| 29 | decision | Decision | flowchart | same as 4 |
| 30 | connector | Connector | flowchart | same as 2 |
| 31 | merge | Merge | flowchart | same as 6 |
| 32 | cloud | Cloud | advanced | icon: placeholder, label below |
| 33 | key | Key | advanced | icon: placeholder, label below |
| 34 | server | Server | advanced | icon: placeholder, label below |
| 35 | archive | Archive | advanced | icon: placeholder, label below |
| 36 | browser | Browser | advanced | icon: placeholder, label below |
| 37 | user | User | advanced | icon: placeholder, label below |
| 38 | compute | Compute | advanced | icon: placeholder, label below |
| 39 | computer | Computer | advanced | icon: placeholder, label below |
| 40 | file | File | advanced | icon: placeholder, label below |
| 41 | firewall | Firewall | advanced | icon: placeholder, label below |
| 42 | folder | Folder | advanced | icon: placeholder, label below |
| 43 | frontend | Frontend | advanced | icon: placeholder, label below |
| 44 | internet | Internet | advanced | icon: placeholder, label below |
| 45 | lock | Lock | advanced | icon: placeholder, label below |
| 46 | mail | Mail | advanced | icon: placeholder, label below |
| 47 | mobile | Mobile | advanced | icon: placeholder, label below |
| 48 | settings | Settings | advanced | icon: placeholder, label below |
| 49 | shield | Shield | advanced | icon: placeholder, label below |
| 50 | users | Users | advanced | icon: placeholder, label below |
| 51 | switch | Switch | advanced | icon: placeholder, label below |
| 52 | database-advanced | Database (advanced; key derived from the enum name) | advanced | icon: placeholder, label below |
| 53 | alternate-process | Alternate process | flowchart | same as 3 |
| 54 | use-case | Use case | uml | same as 2 |
| 55 | classifier | Classifier | uml | same as 16 |
| 56 | note | Note | uml | own drawing |
| 57 | interface-2 | Simple interface | uml | same as 1 |
| 58 | activation | Activation | uml | same as 1 |
| 59 | activity | Activity | uml | own drawing |
| 60 | actor | Actor | uml | own drawing (label below) |
| 61 | assembly | Assembly | uml | own drawing (no text) |
| 62 | component | Component | uml | own drawing |
| 63 | deletion | Deletion | uml | own drawing (no text) |
| 64 | end | End | uml | own drawing (label below) |
| 65 | flow-final | Flow final | uml | same as 14 |
| 66 | gateway | Gateway | uml | same as 4 |
| 67 | history-pseudostate | History pseudostate | uml | own drawing (no text) |
| 68 | horizontal-fork | Horizontal fork | uml | own drawing (no text) |
| 69 | vertical-fork | Vertical fork | uml | own drawing (no text) |
| 70 | off-page-link | Off-page link | uml | own drawing |
| 71 | pin-filled-left | Pin filled left | uml | own drawing (no text) |
| 72 | pin-filled-right | Pin filled right | uml | own drawing (no text) |
| 73 | pin-left | Pin left | uml | own drawing (no text) |
| 74 | pin-right | Pin right | uml | own drawing (no text) |
| 75 | pin | Pin | uml | own drawing (no text) |
| 76 | provided-interface | Provided interface | uml | own drawing (no text) |
| 77 | receive-signal | Receive signal | uml | own drawing |
| 78 | required-interface | Required interface | uml | own drawing (no text) |
| 79 | send-signal | Send signal | uml | own drawing |
| 80 | start | Start | uml | own drawing (label below) |
| 81 | template | Template | uml | same as 1 |
| 82 | class | Class (composite) | uml | own drawing |
| 83 | interface | Interface (composite) | uml | own drawing |
| 84 | node | Node | uml | own drawing |
| 85 | container | Container | uml | own drawing (no text) |
| 86 | boundary-object | Boundary object | uml | own drawing |
| 87 | entity-object | Entity object | uml | own drawing |
| 88 | control-object | Control object | uml | own drawing |

Use `--shape-map` to draw a kind as another, by number or key, e.g.
`--shape-map 'server=database,60=ellipse'`. An icon cannot be a target.

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
- **Dynamic** connectors are right-angled. Through waypoint handles
  `wb2canvas` ports the editor's own algorithm
  (`computeFindOrthogonalPathWithWaypoints`): coordinates alternate between
  x and y, each handle pins its segment, and an end that would leave or
  enter along the wrong axis first steps out of its box by `4w + 10` (`w`
  the line thickness). Without handles it takes a simpler route, leaving
  and entering perpendicular to the box edges; the editor's also avoids
  other shapes. Bends are rounded with radius 10, or half the shorter
  neighbouring segment.

Line thickness `w` is 2, 4 or 6 board units for stroke sizes 1–3. Dashed
lines repeat every `12w` with a 60% dash, round ends included. Dotted lines
are round dots `w` across, every `2.4w`.

### Line ends

The editor's arrowhead table, with the `diagrammingArrowheads` experiment
on (as on the sites examined; with it off, caps are a plain on/off arrow):

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

- **Rounded rectangle:** corner radius `0.1 × w` (the editor's own drawing
  uses a fixed 35.2; see "How the editor draws shapes").
- **Triangle:** apex at top centre; the upside-down variant has its apex at bottom centre.
- **Diamond:** the four edge midpoints.
- **Parallelograms:** the top edge spans the box. The bottom edge is shifted
  by `k × h` with `k = −0.2` (right, leaning `/`) or `+0.2` (left, leaning
  `\`), so the shape extends past its box by 20% of its height.
- **Arrowhead:** filled triangle of size `max(3 × stroke width, 6)`.
- **Dash patterns:** dashed `12`, dotted `4`. The editor's own patterns are
  described above (outlines under "How the editor draws shapes", lines under
  "Connector routing").
- **Routing:** curved connectors pass through the waypoints with no end
  directions, dynamic ones are an orthogonal polyline, straight ones a line.
