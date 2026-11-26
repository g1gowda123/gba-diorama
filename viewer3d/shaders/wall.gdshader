shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_opaque, fog_disabled;

// The diorama's cut edge.  It fades from the map's soil colour at the surface
// into the backdrop, so the island does not end on a hard brown line.

uniform vec4 top_color : source_color = vec4(0.19, 0.14, 0.11, 1.0);
uniform vec4 bottom_color : source_color = vec4(0.016, 0.024, 0.032, 1.0);
uniform float top_y = 0.1;
uniform float bottom_y = -0.9;

varying vec3 world_pos;

float hash21(vec2 p) {
	p = fract(p * vec2(123.34, 456.21));
	p += dot(p, p + 45.32);
	return fract(p.x * p.y);
}

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float t = clamp((world_pos.y - bottom_y) / max(top_y - bottom_y, 0.001), 0.0, 1.0);
	t = pow(t, 0.48);
	vec3 soil = top_color.rgb;
	vec3 rock = bottom_color.rgb;
	vec3 col = mix(rock, soil, t);
	// Layered strata make the cut face read as a cliff rather than a flat
	// brown ribbon.
	float strata_phase = world_pos.y * 5.5 + hash21(floor(vec2(world_pos.x + world_pos.z, 0.0))) * 0.8;
	float band = smoothstep(0.42, 0.5, fract(strata_phase)) * smoothstep(0.58, 0.5, fract(strata_phase));
	col *= 0.82 + band * 0.24;
	// A thin grass lip over the soil face makes the diorama read as a cut
	// piece of land rather than a floating carpet.
	float lip = smoothstep(top_y - 0.14, top_y + 0.01, world_pos.y);
	col = mix(col, vec3(0.20, 0.33, 0.22), lip * 0.85);
	ALBEDO = col;
}
