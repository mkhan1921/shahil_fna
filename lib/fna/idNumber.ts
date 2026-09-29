/**
 * South African identity number (YYMMDD SSSS C A Z) parsing and validation.
 * - YYMMDD: date of birth
 * - SSSS: 0000–4999 female, 5000–9999 male
 * - C: 0 = SA citizen, 1 = permanent resident, 2 = refugee
 * - A: historical race digit, now typically 8 or 9
 * - Z: Luhn check digit
 */

export interface ParsedId {
  valid: boolean;
  error?: string;
  dateOfBirth?: string;
  gender?: 'male' | 'female';
  citizenship?: 'SA citizen' | 'Permanent resident' | 'Refugee';
}

export const luhnValid = (digits: string): boolean => {
  let total = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (double) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
    double = !double;
  }
  return total % 10 === 0;
};

export const parseSaId = (raw: string, today: Date = new Date()): ParsedId => {
  const id = (raw || '').replace(/\s+/g, '');
  if (!id) return { valid: false, error: 'Empty' };
  if (!/^\d{13}$/.test(id)) return { valid: false, error: 'An SA ID number has 13 digits' };

  const yy = Number(id.slice(0, 2));
  const mm = Number(id.slice(2, 4));
  const dd = Number(id.slice(4, 6));
  const currentYY = today.getFullYear() % 100;
  const year = yy <= currentYY ? 2000 + yy : 1900 + yy;
  const date = new Date(Date.UTC(year, mm - 1, dd));
  if (mm < 1 || mm > 12 || date.getUTCMonth() !== mm - 1 || date.getUTCDate() !== dd) {
    return { valid: false, error: 'Date of birth digits are not a real date' };
  }

  const citizenDigit = Number(id[10]);
  if (citizenDigit > 2) return { valid: false, error: 'Citizenship digit must be 0, 1 or 2' };
  if (!luhnValid(id)) return { valid: false, error: 'Check digit does not match (possible typo)' };

  return {
    valid: true,
    dateOfBirth: `${year}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`,
    gender: Number(id.slice(6, 10)) >= 5000 ? 'male' : 'female',
    citizenship: citizenDigit === 0 ? 'SA citizen' : citizenDigit === 1 ? 'Permanent resident' : 'Refugee',
  };
};

/** Age in completed years on `at`. */
export const ageOn = (dateOfBirth: string, at: Date = new Date()): number | null => {
  if (!dateOfBirth) return null;
  const dob = new Date(`${dateOfBirth}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return null;
  let age = at.getFullYear() - dob.getFullYear();
  const m = at.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && at.getDate() < dob.getDate())) age--;
  return age;
};

/** Age next birthday — the basis most SA life insurers price on. */
export const ageNextBirthday = (dateOfBirth: string, at: Date = new Date()): number | null => {
  const age = ageOn(dateOfBirth, at);
  return age === null ? null : age + 1;
};

/** Exact age in fractional years (for projections). */
export const exactAge = (dateOfBirth: string, at: Date = new Date()): number | null => {
  if (!dateOfBirth) return null;
  const dob = new Date(`${dateOfBirth}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return null;
  return (at.getTime() - dob.getTime()) / (365.2425 * 24 * 3600 * 1000);
};
