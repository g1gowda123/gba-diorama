shader_type canvas_item;

// Screen-space tilt-shift that only touches the outer frame.  The centre stays
// texel-crisp, so the pixel art keeps its edges while the diorama gains the
// miniature-photograph falloff that reads as HD-2D.

uniform sampler2D screen_tex : hint_screen_texture, repeat_disable, filter_nearest;
uniform float amount : hint_range(0.0, 6.0) = 1.0;
uniform vec2 focus_center = vec2(0.5, 0.52);
uniform float inner_radius : hint_range(0.0, 1.0) = 0.45;
uniform float outer_radius : hint_range(0.0, 1.0) = 0.85;

void fragment() {
	vec2 uv = SCREEN_UV;
	vec2 delta = uv - focus_center;
	delta.x *= 1.15;
	float distance = length(delta);
	float blur = smoothstep(inner_radius, outer_radius, distance) * amount;
	vec4 color = texture(screen_tex, uv);
	if (blur > 0.35) {
		vec2 step = SCREEN_PIXEL_SIZE * blur;
		vec4 sum = color * 2.0;
		sum += texture(screen_tex, uv + vec2(step.x, 0.0));
		sum += texture(screen_tex, uv - vec2(step.x, 0.0));
		sum += texture(screen_tex, uv + vec2(0.0, step.y));
		sum += texture(screen_tex, uv - vec2(0.0, step.y));
		sum += texture(screen_tex, uv + step * 0.7);
		sum += texture(screen_tex, uv - step * 0.7);
		color = sum / 8.0;
	}
	COLOR = color;
}
