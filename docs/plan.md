# Plan

Where the export stands against the live editor, and what is left. The
rules behind each item are in
[confluence-whiteboard-model.md](confluence-whiteboard-model.md).

## Done

Checked against the five boards of a sample space (Sample Board,
Sample Board 2, Sample Board 3, Sample Board 4, Sample Board 5), side by side with the live editor:

- Shapes, outlines and dashes from the editor's own shape definitions.
- Box sizes: shapes keep their basis width and grow only downward to fit
  their text; empty shapes are never grown.
- Text: the editor's type size, line height and font, inside each shape's
  content box. Every box holds its text and line breaks match.
- Colours: the stored legacy palette drawn in the editor's current theme.
- Connectors: curved, straight and right-angled routes, bend handles,
  rounded bends, line ends, and labels on the line.
- Images, including ones still loading when the board was copied.
- Library icons (e.g. AWS) as named placeholders.
- The Confluence API token: a clear message when it is rejected.

## Remaining

- **Right-angled connectors without bend handles** take a simpler route
  than the editor's, which also steers around other shapes.
- **Library icon artwork** (AWS and other collections) is not available;
  icons are drawn as a tile with their name.
- **Images on connectors** whose picture cannot be downloaded are drawn as
  placeholders, with a warning.
- **The font** is Atlassian Sans only where it is installed; elsewhere a
  system font stands in, so line breaks can differ slightly.
- **Unsupported element types** (stickies, sections, tables, mind maps,
  cards, Jira issues, …) are left out with a warning. None occur on the
  sample boards; each needs a sample board to support.
- **Dark theme**: colours are resolved against the light theme only.

## Next

- Copy the exported space into the Obsidian vault
  (`your Obsidian vault`); folder to be agreed.
