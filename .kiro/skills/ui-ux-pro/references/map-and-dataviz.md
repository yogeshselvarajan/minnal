# Map and data visualisation

## Map (MapLibre GL via react-map-gl/maplibre, Amazon Location style)
- Base style: Amazon Location dark style in dark theme, light style in light theme; desaturate the base so data layers carry the colour.
- **Layer order (bottom → top):** base → flood polygons (fill 25% `--flood` + diagonal hatch pattern + 1.5 px outline) → feeders (lines coloured by energised / de-energised / unknown with dash patterns, not colour alone) → outage clusters (circles with count labels) → suspected failed devices (diamond symbol) → critical facilities (typed icons) → crews (arrow icon with heading) → selection/hover.
- Clustering for outages (`cluster: true`, radius 50); click a cluster to zoom; counts in tabular figures.
- Update data with `source.setData` or feature state; never tear down and rebuild layers.
- Legend always visible (collapsible on phones), grouped by layer with the same symbols as the map.
- Map controls in the top-right; attribution kept (Amazon Location, © OpenStreetMap contributors).
- Keyboard: map focusable, arrow keys pan, `+`/`-` zoom; every map feature is also reachable from a list (accessibility equivalent).

## Charts (shadcn charts / Recharts)
- Use a chart only when the trend or comparison is the point: customers out over time (area, stepped), ETR distribution by area (horizontal bar), crew utilisation (stacked bar).
- Status colours from tokens; max 5 series; direct labels over legends where possible; axis labels with units; tooltips with exact values and time.
- Sparklines in KPI tiles: no axes, last value labelled.
- Look up Recharts and shadcn chart APIs with Context7 before building a chart.
