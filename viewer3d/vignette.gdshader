shader_type canvas_item;

uniform float strength : hint_range(0.0, 1.0) = 0.55;
uniform vec4 tint : source_color = vec4(0.01, 0.015, 0.03, 1.0);

void fragment() {
	vec2 uv = UV;
	float d = distance(uv, vec2(0.5));
	float a = smoothstep(0.28, 0.78, d) * strength;
	COLOR = vec4(tint.rgb, a);
}
