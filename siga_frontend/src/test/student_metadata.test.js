import { describe, it, expect } from 'vitest';
import { readStudentProfile } from '../modules/students/studentMetadata';

describe('readStudentProfile', () => {
  it('lee la fila cruda del Excel de Admision', () => {
    const p = readStudentProfile({
      celular: null,
      metadata_admision: {
        'SEXO: ': 'F',
        'NACIONALIDAD': 'PERUANO',
        'DEPARTAMENTO:': 'UCAYALI ',
        'PROVINCIA:': 'CORONEL PORTILLO',
        'DISTRITO:': 'CALLERIA',
        'DOMICILIO:': 'JR. HUASCAR 123',
        'FECHA NACIMIENTO: ': '2008-05-06T00:00:00',
        'INSTITUCION EDUCATIVA:': 'FAUSTINO MALDONADO',
        'Año que termino Secundaria:': 2025,
        'NOMBRE DEL APODERADO:': 'ANA RIOS',
        'CELULAR (APODERADO):': '987654321',
        'DISCAPACIDAD SI/NO:': 'NO',
        'TIPO DE DISCAPACIDAD:': 'NINGUNA',
        'CELULAR': '999888777',
        'CORREO:': 'None',
      },
    });
    expect(p.genero).toBe('Femenino');
    expect(p.nacionalidad).toBe('Peruana');
    // Se ajusta al catalogo de ubigeo para que los selectores lo reconozcan.
    expect([p.departamento, p.provincia, p.distrito]).toEqual(['Ucayali', 'Coronel Portillo', 'Callería']);
    expect(p.direccion).toBe('JR. HUASCAR 123');
    expect(p.fechaNacimiento).toBe('2008-05-06');
    expect(p.colegio).toBe('FAUSTINO MALDONADO');
    expect(p.anioEgreso).toBe('2025');
    expect(p.contactoNombre).toBe('ANA RIOS');
    expect(p.contactoTelefono).toBe('987654321');
    expect(p.celular).toBe('999888777');
    expect(p.email).toBeNull();
    expect(p.discapacidad).toBe(false);
    expect(p.discapacidadConocida).toBe(true);
  });

  it('prioriza columnas y claves normalizadas del registro SIGA', () => {
    const p = readStudentProfile({
      fecha_nacimiento: '2001-02-03',
      direccion_domicilio: 'Av. Centenario 1',
      metadata_admision: {
        genero: 'Masculino', 'SEXO: ': 'F',
        salud_procedencia: { colegio_procedencia: 'IE 64001' },
        emergencia: { contacto_nombre: 'Luis', contacto_telefono: '911' },
      },
    });
    expect(p.genero).toBe('Masculino');
    expect(p.fechaNacimiento).toBe('2001-02-03');
    expect(p.direccion).toBe('Av. Centenario 1');
    expect(p.colegio).toBe('IE 64001');
    expect(p.contactoNombre).toBe('Luis');
  });

  it('no inventa datos cuando faltan', () => {
    const p = readStudentProfile({ metadata_admision: null });
    expect(p.genero).toBeNull();
    expect(p.nacionalidad).toBeNull();
    expect(p.estadoCivil).toBeNull();
    expect(p.discapacidadConocida).toBe(false);
  });
});
