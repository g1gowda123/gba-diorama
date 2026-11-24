shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_never, depth_test_disabled, blend_mix;

uniform sampler2D albedo_tex : source_color, filter_nearest;
uniform float dither_amount : hint_range(0.0, 1.0) = 0.5;

void vertex() {
	MODELVIEW_MATRIX = VIEW_MATRIX * mat4(
		vec4(length(MODEL_MATRIX[0].xyz), 0.0, 0.0, 0.0),
		vec4(0.0, length(MODEL_MATRIX[1].xyz), 0.0, 0.0),
		vec4(0.0, 0.0, length(MODEL_MATRIX[2].xyz), 0.0),
		vec4(MODEL_MATRIX[3].xyz, 1.0)
	);
}

void fragment() {
	vec4 c = texture(albedo_tex, UV);
	vec2 texel = 1.0 / vec2(textureSize(albedo_tex, 0));
	if (c.a < 0.5) {
		// A one-texel dark rim keeps the dither silhouette readable over
		// busy grass/tree textures.
		float rim = 0.0;
		for (int x = -1; x <= 1; x++) {
			for (int y = -1; y <= 1; y++) {
				if (texture(albedo_tex, UV + vec2(float(x), float(y)) * texel).a > 0.5) {
					rim = 1.0;
				}
			}
		}
		if (rim > 0.5) {
			ALBEDO = vec3(0.02, 0.02, 0.02);
			ALPHA = 0.9;
		} else {
			discard;
		}
	} else {
		float checker = mod(floor(FRAGCOORD.x * 0.5) + floor(FRAGCOORD.y * 0.5), 2.0);
		if (checker < dither_amount) {
			discard;
		}
		ALBEDO = c.rgb * 0.9;
		ALPHA = c.a;
	}
}
