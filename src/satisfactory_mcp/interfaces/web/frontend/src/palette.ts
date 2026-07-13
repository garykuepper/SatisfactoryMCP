/* Every colour the page chooses, in one file, because they were chosen against each other.
 *
 * The measured ones say so where they are declared -- the pipe rust and the chevron cream
 * were picked in CIE Lab against the terrain and against every other colour here -- and that
 * argument only holds while the colours it compares are in one place to be compared. A new
 * layer's colour is a decision about this table, not about that layer.
 *
 * The route and structure colours are the deliberate exception: they live beside the network
 * they belong to, because each is stated together with the width, the tier ramp and the
 * reasoning that made it, and splitting that across two files would leave the colour here
 * and its warrant there.
 */

// Ore colours follow the in-game item tints closely enough to be recognisable without
// shipping a single game asset: they are hex strings, not textures.
export var RESOURCE_COLOUR: Record<string, string> = {
  Desc_OreIron_C: "#c8b6a6",
  Desc_OreCopper_C: "#e08a4b",
  Desc_Stone_C: "#cfcfcf",
  Desc_Coal_C: "#4c4c4c",
  Desc_OreGold_C: "#e3c74a",
  Desc_Sulfur_C: "#e8e35c",
  Desc_RawQuartz_C: "#e59ce0",
  Desc_OreBauxite_C: "#b06a4a",
  Desc_OreUranium_C: "#7ce07c",
  Desc_LiquidOil_C: "#6b4bb0",
  Desc_NitrogenGas_C: "#6ec5e0",
  Desc_Water_C: "#3f8fd0",
  Desc_SAM_C: "#b04bd0",
  Desc_Geyser_C: "#d97b4f", // synthetic label; a geyser is a placement target, not an item
};

export var PURITY_RADIUS: Record<string, number> = { impure: 3, normal: 4.5, pure: 6 };

export var KIND_COLOUR: Record<string, string> = { machines: "#4aa3df", extractors: "#e0a33f", generators: "#d9534f" };

/* Storage, and picked the way the pipe rust was: by measuring, not by taste.
 *
 * A container is drawn as a filled footprint box, so the colours it has to separate from are
 * the other filled boxes -- the three machine kinds, the belt attachments, and the concrete it
 * stands on -- and then, more weakly, everything else on the page. Magenta is what is left: the
 * page already spends blue on machines, amber on extractors, red on generators, steel on belts
 * and rust on pipes, and the whole warm half is taken.
 *
 * In CIE Lab, #ad4f96 is dE 51.8 from its nearest filled box (the generator red) and 48.5 from
 * the nearest biome ground, which are the two comparisons that decide whether a box reads. Its
 * nearest neighbour ANYWHERE on the page is the raw-quartz node dot at dE 27.4 -- a small disc
 * on open terrain rather than a rectangle inside a factory, so the two are never asked to be
 * told apart in the same square metre. The alternatives measured beside it were all worse on
 * one of the two: a lighter magenta (#c76bb0) lands dE 17.7 from that same quartz dot, a violet
 * (#8c72c4) dE 19.1 from the crude-oil dot and only 37.3 from the machine blue, and a sea green
 * dE 10.5 from the pickup teal.
 */
export var STORAGE_COLOUR = "#ad4f96";

/* The fluid buffers, one value step down the same hue -- the grammar the belts and pipes use
 * for their tiers, borrowed for a distinction that is not a tier: a tank and a box are two
 * kinds of container rather than two grades of one, and one family with a step inside it says
 * "same layer, different thing" without spending a second hue on it.
 *
 * The step is the house step: dE 16.7, against the belts' 15.6 between their slowest and
 * fastest and the pipes' 15.7 between Mk1 and Mk2. Re-measured rather than assumed safe,
 * because a ramp can walk a colour into a neighbour -- this one moves AWAY from everything,
 * ending dE 34.0 from its nearest colour on the page (the crude-oil dot) and 37.6 from the
 * nearest ground, both further off than the box tone above.
 */
export var STORAGE_FLUID_COLOUR = "#7f3169";

// One colour per pickup category, so ten separate checkboxes stop drawing one
// indistinguishable teal dot. Unlisted categories share the old teal as the fallback.
export var PICKUP_COLOUR: Record<string, string> = {
  somersloop: "#e05c5c",
  mercer_sphere: "#b06ae0",
  hard_drive: "#6ea8d8",
  loot_cache: "#d8b46e",
  crashed_drop_pod: "#9aa8b8",
  power_slug_blue: "#5cc8e8",
  power_slug_yellow: "#e8d55c",
  power_slug_purple: "#c85ce8",
  mushroom: "#a8c86e",
  tape_pickup: "#e09a6e",
};
export var PICKUP_FALLBACK = "#7fd1b9";

export var PLAYER_COLOUR = "#f5f0e8";

// One muted colour per biome letter, keyed exactly like /api/regions' legend. Hand-picked
// to read as ground at a glance -- sand for the deserts, greens for the forests, teal
// along the coast, murk for the swamp -- and, like the ore palette above, they are 21 hex
// strings rather than a single pixel of anyone's artwork.
//
// Dark on purpose, and every CELL is painted at full opacity, always: a translucent cell
// has to blend against whatever is at its edges too, and 768 of them sharing borders turns
// that blend into a visible 256 m grid. These are the blended values, baked in, so the
// cells of one region merge into one shape.
//
// Transparency over the map render is therefore NOT done here -- see REGION_BLEND in regions.ts, which
// fades the finished composite once, at the pane.
/* Keyed by the legend letter `data/region_names.json` assigns, which is alphabetical by
 * region name -- so the letters moved when the region layer was re-derived from the game's
 * own map areas and three wiki-only names went with it. Every colour below is the one that
 * region already had; what changed is which letter it hangs on, plus one new entry.
 *
 * Gone: Eastern Dune Forest (#4e5c3d), Snaketree Forest (#415037), Western Beaches
 * (#736d56). The game names none of those three anywhere on the map.
 */
export var REGION_COLOUR: Record<string, string> = {
  A: "#3e3e3c", // Abyss Cliffs
  B: "#284e5a", // Blue Crater
  C: "#2e5348", // Crater Lakes
  D: "#654e37", // Desert Canyons
  E: "#726443", // Dune Desert
  F: "#3b5a3b", // Grass Fields
  G: "#294834", // Jungle Spires
  H: "#32544d", // Lake Forest
  I: "#594a37", // Maze Canyons
  /* No Man's Land: the game's own name for the outer coast and the ocean, and 287 of the
   * 768 painted cells -- so it is the largest thing on this layer and the one that must NOT
   * read as a biome. Bare, pale and desaturated, one step brighter than any ground here.
   *
   * Measured like the rest of this file. In CIE Lab it is dE 17.1 from its nearest
   * neighbour (Rocky Desert, which it borders for most of the west coast), 18.4 from Dune
   * Desert and 20.6 from Western Dune Forest -- above the ~15.6 step the belts use and
   * comfortably above the pipes' 15.7. The alternatives measured beside it were all worse
   * against that same Rocky Desert border: the render's own no-man's-land tone (#7c7a6c)
   * lands at dE 12.6, a warm sand (#807a68) at 13.3, and anything darker collapses onto it
   * (#5a5750 is dE 3.9). Cool greys were rejected for the other end: #46484a is dE 5.1
   * from Abyss Cliffs. */
  J: "#8a8478", // No Man's Land
  K: "#2e4637", // Northern Forest
  L: "#65423b", // Red Bamboo Fields
  M: "#4e3937", // Red Jungle
  N: "#5c5b4e", // Rocky Desert
  O: "#335041", // Southern Forest
  P: "#295258", // Spire Coast
  Q: "#374232", // Swamp
  R: "#294233", // Titan Forest
  S: "#585d40", // Western Dune Forest
};
