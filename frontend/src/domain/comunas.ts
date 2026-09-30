// Archivo generado por `scripts/generar-comunas.mjs` desde
// `backend/migrations/data/comunas_v_region.geojson`. No editar a mano.

interface Comuna {
  cut: number
  nombre: string
  provincia: string
  /** Un punto dentro de la comuna, [lon, lat]. */
  punto: [number, number]
  /** Caja que la contiene: [oeste, sur, este, norte]. */
  caja: [number, number, number, number]
}

export const COMUNAS: readonly Comuna[] = [
  {cut: 5602, nombre: 'Algarrobo', provincia: 'San Antonio', punto: [-71.5988, -33.3295], caja: [-71.6958, -33.4033, -71.5223, -33.252]},
  {cut: 5402, nombre: 'Cabildo', provincia: 'Petorca', punto: [-70.8237, -32.4173], caja: [-71.1432, -32.6346, -70.4114, -32.2128]},
  {cut: 5302, nombre: 'Calle Larga', provincia: 'Los Andes', punto: [-70.5447, -32.9508], caja: [-70.6772, -33.0892, -70.4067, -32.8252]},
  {cut: 5603, nombre: 'Cartagena', provincia: 'San Antonio', punto: [-71.4421, -33.5338], caja: [-71.6275, -33.6096, -71.328, -33.4518]},
  {cut: 5102, nombre: 'Casablanca', provincia: 'Valparaíso', punto: [-71.4347, -33.3159], caja: [-71.7078, -33.4892, -71.1955, -33.1552]},
  {cut: 5702, nombre: 'Catemu', provincia: 'San Felipe', punto: [-70.9446, -32.7074], caja: [-71.0551, -32.8287, -70.8194, -32.6073]},
  {cut: 5103, nombre: 'Concón', provincia: 'Valparaíso', punto: [-71.4668, -32.9526], caja: [-71.5541, -33.0021, -71.3871, -32.9139]},
  {cut: 5604, nombre: 'El Quisco', provincia: 'San Antonio', punto: [-71.6513, -33.4152], caja: [-71.7091, -33.446, -71.5891, -33.3719]},
  {cut: 5605, nombre: 'El Tabo', provincia: 'San Antonio', punto: [-71.5803, -33.4829], caja: [-71.6801, -33.5322, -71.4774, -33.429]},
  {cut: 5503, nombre: 'Hijuelas', provincia: 'Quillota', punto: [-71.0811, -32.8694], caja: [-71.1801, -32.9906, -71.0021, -32.7359]},
  {cut: 5502, nombre: 'La Calera', provincia: 'Quillota', punto: [-71.2006, -32.7939], caja: [-71.2299, -32.8673, -71.0844, -32.7493]},
  {cut: 5504, nombre: 'La Cruz', provincia: 'Quillota', punto: [-71.2402, -32.825], caja: [-71.3303, -32.8696, -71.1454, -32.7791]},
  {cut: 5401, nombre: 'La Ligua', provincia: 'Petorca', punto: [-71.2709, -32.3538], caja: [-71.5415, -32.621, -71.046, -32.1273]},
  {cut: 5802, nombre: 'Limache', provincia: 'Marga Marga', punto: [-71.2781, -33.0312], caja: [-71.4429, -33.1616, -71.1335, -32.918]},
  {cut: 5703, nombre: 'Llaillay', provincia: 'San Felipe', punto: [-70.9017, -32.8881], caja: [-71.0401, -32.979, -70.7282, -32.7989]},
  {cut: 5301, nombre: 'Los Andes', provincia: 'Los Andes', punto: [-70.2431, -32.9509], caja: [-70.6592, -33.1876, -69.9891, -32.725]},
  {cut: 5506, nombre: 'Nogales', provincia: 'Quillota', punto: [-71.1761, -32.6916], caja: [-71.3189, -32.8118, -71.0333, -32.5913]},
  {cut: 5803, nombre: 'Olmué', provincia: 'Marga Marga', punto: [-71.1104, -33.0357], caja: [-71.2223, -33.1212, -71.0009, -32.9546]},
  {cut: 5704, nombre: 'Panquehue', provincia: 'San Felipe', punto: [-70.8285, -32.7938], caja: [-70.9484, -32.8345, -70.7367, -32.7313]},
  {cut: 5403, nombre: 'Papudo', provincia: 'Petorca', punto: [-71.3798, -32.4748], caja: [-71.4629, -32.5534, -71.2842, -32.4022]},
  {cut: 5404, nombre: 'Petorca', provincia: 'Petorca', punto: [-70.8699, -32.1904], caja: [-71.1826, -32.386, -70.4458, -32.0209]},
  {cut: 5105, nombre: 'Puchuncaví', provincia: 'Valparaíso', punto: [-71.3875, -32.7454], caja: [-71.5096, -32.8627, -71.2774, -32.6268]},
  {cut: 5705, nombre: 'Putaendo', provincia: 'San Felipe', punto: [-70.5216, -32.4812], caja: [-70.8428, -32.7145, -70.2174, -32.2538]},
  {cut: 5501, nombre: 'Quillota', provincia: 'Quillota', punto: [-71.2726, -32.9047], caja: [-71.4522, -32.974, -71.1195, -32.8303]},
  {cut: 5801, nombre: 'Quilpué', provincia: 'Marga Marga', punto: [-71.2542, -33.1472], caja: [-71.4934, -33.2465, -70.9916, -33.0006]},
  {cut: 5107, nombre: 'Quintero', provincia: 'Valparaíso', punto: [-71.4726, -32.8431], caja: [-71.5469, -32.9272, -71.3883, -32.7628]},
  {cut: 5303, nombre: 'Rinconada', provincia: 'Los Andes', punto: [-70.7062, -32.8763], caja: [-70.7679, -32.9605, -70.6492, -32.7869]},
  {cut: 5601, nombre: 'San Antonio', provincia: 'San Antonio', punto: [-71.4892, -33.6649], caja: [-71.6309, -33.7891, -71.3291, -33.5521]},
  {cut: 5304, nombre: 'San Esteban', provincia: 'Los Andes', punto: [-70.3483, -32.6867], caja: [-70.6137, -32.91, -70.1231, -32.4291]},
  {cut: 5701, nombre: 'San Felipe', provincia: 'San Felipe', punto: [-70.753, -32.7363], caja: [-70.871, -32.8256, -70.6345, -32.6454]},
  {cut: 5706, nombre: 'Santa María', provincia: 'San Felipe', punto: [-70.6097, -32.6861], caja: [-70.684, -32.8138, -70.5137, -32.5694]},
  {cut: 5606, nombre: 'Santo Domingo', provincia: 'San Antonio', punto: [-71.6763, -33.8098], caja: [-71.8431, -33.9561, -71.536, -33.6163]},
  {cut: 5101, nombre: 'Valparaíso', provincia: 'Valparaíso', punto: [-71.5732, -33.1297], caja: [-71.7457, -33.2138, -71.3834, -33.0182]},
  {cut: 5804, nombre: 'Villa Alemana', provincia: 'Marga Marga', punto: [-71.3302, -33.0675], caja: [-71.4009, -33.1219, -71.2565, -33.0115]},
  {cut: 5109, nombre: 'Viña del Mar', provincia: 'Valparaíso', punto: [-71.5152, -33.0278], caja: [-71.5871, -33.1047, -71.4428, -32.9452]},
  {cut: 5405, nombre: 'Zapallar', provincia: 'Petorca', punto: [-71.3359, -32.5875], caja: [-71.4766, -32.6834, -71.2011, -32.498]},
]
