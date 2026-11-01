# Visual direction

## The front page

A real wildlife image carries the post. Overlay a small **THE WILDLIFE DESK** masthead, a short section label when useful, and a large headline. Use a subtle dark gradient where the text needs contrast.

The [hero example](../assets/wildlife-desk-hero.jpg) establishes the mood: restrained monochrome, a news masthead, strong type, and an unobscured animal. It is an AI-generated illustration of the layout, not a real sighting or a renderer output.

## Proposed layout defaults

| Element | Direction |
| --- | --- |
| Source media | Preserve original camera evidence; color and infrared captures both work. |
| Masthead | Small white uppercase text, optionally with a thin rule. |
| Headline | Bold condensed sans-serif, white, with deliberate line wrapping. |
| Contrast | Local dark gradient or restrained translucent panel. |
| Section label | Small uppercase text such as THE NIGHT SHIFT. |
| Palette | White, near-black, and the source photograph's natural colors. |
| Composition | Move the text to fit the animal; avoid a fixed overlay that hides the subject. |
| Decoration | Minimal. No fake live badges, fabricated timestamps, or news ticker clutter. |

Start with a 1080 by 1350 portrait cover as a design target. This is a proposed internal canvas, not a claim about current platform requirements. Confirm current publishing and profile-crop behavior before implementation. The landscape README hero demonstrates the visual direction separately.

## Rendering requirements

Render text deterministically from an approved headline. Wrap by measured font width, enforce margins, and reject overflow. Include licensed fonts with clear redistribution terms when selecting actual font files.

Keep the animal's face and important behavior visible. Preview covers both at full size and as small thumbnails, including likely profile crops. Reposition text or choose another frame if readability and subject visibility conflict.

Use the original photograph as the source. Cropping, ordinary exposure adjustments, and overlays must preserve the evidence. Do not add animals, remove inconvenient context, or generate missing scene content. Remove exported location metadata and inspect for identifying visual details before public output.

For video, use a representative frame for the cover and retain the relevant sequence. A short video and a still image can share the same headline system. Video editing and publishing are later extensions.

## Example caption pairing

Headline: **RACCOON RETURNS TO PROPERTY, OFFERS NO EXPLANATION**

The caption expands the observation rather than repeating the headline word for word. Use two or three short paragraphs: the event, a supported detail or earlier sighting, then a dry closing sentence.

## Asset provenance

The prompt and provenance for the current mockup are recorded in [assets/README.md](../assets/README.md). Future examples must say whether they use authentic camera media, synthetic illustration, or renderer output. Do not imply the planning-stage mockup came from an implemented pipeline.
