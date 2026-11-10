import { GEN_GLSL } from "./generators";

export const VERTEX_SRC = `#version 300 es
in vec2 a_pos;
in vec3 a_uvw;            // mesh path: (u, v, 1) / w
uniform vec2 u_size;
out vec2 v_pos;
out vec3 v_uvw;
void main() {
  gl_Position = vec4(a_pos.x / u_size.x * 2.0 - 1.0, 1.0 - a_pos.y / u_size.y * 2.0, 0.0, 1.0);
  v_pos = a_pos;
  v_uvw = a_uvw;
}
`;

export const FRAGMENT_SRC = `#version 300 es
precision highp float;
in vec2 v_pos;
in vec3 v_uvw;
uniform float u_meshOn;   // 1 = uv comes from the tessellated mesh, 0 = from u_invH
uniform sampler2D u_tex;
uniform mat3 u_invH;      // projector px -> surface unit square (column-major)
uniform vec4 u_src;       // normalised source rect x0,y0,x1,y1
uniform vec4 u_dst;       // dst rect in the surface unit square
uniform float u_opacity;  // clip opacity * fade
uniform vec2 u_featherUv; // feather width in uv units (0 = none)
uniform sampler2D u_mask; // polygon mask (R8, half projector size)
uniform float u_maskOn;   // 1 = multiply alpha by the mask, 0 = ignore
uniform vec2 u_size;      // projector size in px
out vec4 outColor;
${GEN_GLSL}
void main() {
  vec3 q = u_invH * vec3(v_pos, 1.0);
  vec2 uv = u_meshOn > 0.5 ? v_uvw.xy / v_uvw.z : q.xy / q.z;
  if (uv.x < u_dst.x || uv.y < u_dst.y || uv.x > u_dst.z || uv.y > u_dst.w) discard;
  vec2 t = (uv - u_dst.xy) / (u_dst.zw - u_dst.xy);
  vec2 st = mix(u_src.xy, u_src.zw, t);
  vec4 c = u_genType == 0 ? texture(u_tex, st) : genColor(uv);
  float feather = 1.0;
  if (u_featherUv.x > 0.0 && u_featherUv.y > 0.0) {
    vec2 d = min(uv, 1.0 - uv) / u_featherUv;
    feather = smoothstep(0.0, 1.0, min(d.x, d.y));
  }
  float m = u_maskOn > 0.5 ? texture(u_mask, v_pos / u_size).r : 1.0;
  float a = c.a * u_opacity * feather * m;
  outColor = vec4(c.rgb * a, a);
}
`;
