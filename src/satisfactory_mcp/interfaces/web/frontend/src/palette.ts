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
export var REGION_COLOUR: Record<string, string> = {
  A: "#3e3e3c", // Abyss Cliffs
  B: "#284e5a", // Blue Crater
  C: "#2e5348", // Crater Lakes
  D: "#654e37", // Desert Canyons
  E: "#726443", // Dune Desert
  F: "#4e5c3d", // Eastern Dune Forest
  G: "#3b5a3b", // Grass Fields
  H: "#294834", // Jungle Spires
  I: "#32544d", // Lake Forest
  J: "#594a37", // Maze Canyons
  K: "#2e4637", // Northern Forest
  L: "#65423b", // Red Bamboo Fields
  M: "#4e3937", // Red Jungle
  N: "#5c5b4e", // Rocky Desert
  O: "#415037", // Snaketree Forest
  P: "#335041", // Southern Forest
  Q: "#295258", // Spire Coast
  R: "#374232", // Swamp
  S: "#294233", // Titan Forest
  T: "#736d56", // Western Beaches
  U: "#585d40", // Western Dune Forest
};
