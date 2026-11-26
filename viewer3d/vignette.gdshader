shader_type canvas_item;

uniform float strength : hint_range(0.0, 1.0) = 0.50;
uniform vec4 tint : source_color = vec4(0.004, 0.008, 0.016, 1.0);

void fragment() {
	vec2 uv = UV;
	float d = distance(uv, vec2(0.5));
	float a = smoothstep(0.30, 0.86, d) * strength;
	COLOR = vec4(tint.rgb, a);
}
