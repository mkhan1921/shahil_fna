'use client';

import React, { useState, useEffect } from 'react';
import { FamilyMember } from '../types';

interface MemberFormProps {
  isPrimary: boolean;
  memberData: FamilyMember;
  onChange: (data: FamilyMember) => void;
  onRemove?: () => void;
}

const MemberForm: React.FC<MemberFormProps> = ({ isPrimary, memberData, onChange, onRemove }) => {
  const [isOtherTitle, setIsOtherTitle] = useState(false);
  const [isSACitizen, setIsSACitizen] = useState(true);

  useEffect(() => {
    const hasPassport = Boolean(memberData.passportNumber || memberData.countryOfBirth);
    const hasId = Boolean(memberData.idNumber);
    if (hasPassport && !hasId) {
      setIsSACitizen(false);
    } else if (hasId && !hasPassport) {
      setIsSACitizen(true);
    }
  }, [memberData.passportNumber, memberData.countryOfBirth, memberData.idNumber]);

  // Auto-calculate on ID change
  useEffect(() => {
    if (memberData.idNumber && memberData.idNumber.length === 13 && isSACitizen) {
      // Direct parsing from ID string (YYMMDD)
      const yearStr = memberData.idNumber.substring(0, 2);
      const monthStr = memberData.idNumber.substring(2, 4);
      const dayStr = memberData.idNumber.substring(4, 6);
      const genderCode = parseInt(memberData.idNumber.substring(6, 10), 10);

      const year = parseInt(yearStr, 10);
      const month = parseInt(monthStr, 10);
      const day = parseInt(dayStr, 10);

      // Basic validation - ensure valid month and day
      if (month < 1 || month > 12 || day < 1 || day > 31 || isNaN(year)) return;

      const currentYear = new Date().getFullYear();
      const currentYearShort = currentYear % 100;

      // ID Century Logic: If YY > current YY, it's 19YY, else 20YY
      let fullYear = year > currentYearShort ? 1900 + year : 2000 + year;

      // Fix for Centenarians (primary members likely > 18)
      if (isPrimary && (currentYear - fullYear) < 18) {
        fullYear -= 100;
      }

      const dobDate = new Date(fullYear, month - 1, day);
      const today = new Date();

      // Calculate Age
      let age = today.getFullYear() - dobDate.getFullYear();
      const m = today.getMonth() - dobDate.getMonth();
      if (m < 0 || (m === 0 && today.getDate() < dobDate.getDate())) {
        age--;
      }

      // Calculate Days to Next Birthday
      const nextBirthday = new Date(today.getFullYear(), dobDate.getMonth(), dobDate.getDate());
      if (today > nextBirthday) {
        nextBirthday.setFullYear(today.getFullYear() + 1);
      }

      const oneDay = 24 * 60 * 60 * 1000;
      const diffTime = nextBirthday.getTime() - today.getTime();
      const daysToNextBirthday = Math.ceil(diffTime / oneDay);

      // Format Date: DD/MM/YYYY
      const formattedDOB = `${dayStr}/${monthStr}/${fullYear}`;

      // Month Abbreviation
      const monthNames = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
      const monthAbbr = monthNames[month - 1];

      onChange({
        ...memberData,
        gender: genderCode >= 5000 ? 'Male' : 'Female',
        dateOfBirth: formattedDOB,
        dobMonthAbbr: monthAbbr,
        currentAge: age,
        ageNextBirthday: age + 1,
        daysToNextBirthday: daysToNextBirthday
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberData.idNumber, isSACitizen]);

  const handleTitleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const value = e.target.value;
    if (value === 'Other') {
      setIsOtherTitle(true);
      onChange({ ...memberData, title: '' });
    } else {
      setIsOtherTitle(false);
      onChange({ ...memberData, title: value });
    }
  };

  // Shared classes for grid cells
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "px-3 py-2 relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif";

  return (
    <div className="border-b border-gray-200">
      {/* Section Header - Minimal */}
      <div className="px-3 py-2 border-b border-gray-200 flex justify-between items-center">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">
          {isPrimary ? 'Primary Member Details' : 'Family Member Details'}
        </h3>
        {!isPrimary && (
          <button 
            onClick={onRemove}
            type="button"
            className="text-red-600 text-[10px] hover:text-red-800 uppercase font-bold"
          >
            Remove
          </button>
        )}
      </div>

      {/* Row 1: Title, Names */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-2 ${labelCellClass}`}>Title</div>
        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
          {isOtherTitle ? (
             <div className="flex gap-1">
               <input 
                 type="text" 
                 value={memberData.title}
                 onChange={(e) => onChange({...memberData, title: e.target.value})}
                 className={inputClass}
                 placeholder="Title"
                 autoFocus
               />
               <button type="button" onClick={() => setIsOtherTitle(false)} className="text-[10px] text-blue-800">×</button>
             </div>
          ) : (
            <select 
              value={memberData.title} 
              onChange={handleTitleChange}
              className={`${inputClass} cursor-pointer`}
            >
              <option value="">Select</option>
              <option value="Mr.">Mr.</option>
              <option value="Mrs.">Mrs.</option>
              <option value="Ms.">Ms.</option>
              <option value="Miss">Miss</option>
              <option value="Dr.">Dr.</option>
              <option value="Prof.">Prof.</option>
              <option value="Rev.">Rev.</option>
              <option value="Other">Other</option>
            </select>
          )}
        </div>

        <div className={`col-span-2 ${labelCellClass}`}>First Name</div>
        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
          <input 
            type="text"
            value={memberData.firstName}
            onChange={(e) => onChange({...memberData, firstName: e.target.value})}
            className={inputClass}
          />
        </div>

        <div className={`col-span-2 ${labelCellClass}`}>Surname</div>
        <div className={`col-span-2 ${inputCellClass}`}>
          <input 
            type="text"
            value={memberData.surname}
            onChange={(e) => onChange({...memberData, surname: e.target.value})}
            className={inputClass}
          />
        </div>
      </div>

      {/* Row 2: Relationship (if not primary) */}
      {!isPrimary && (
        <>
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Relationship</div>
            <div className={`col-span-9 ${inputCellClass}`}>
              <select
                value={memberData.relationship}
                onChange={(e) => onChange({...memberData, relationship: e.target.value})}
                className={`${inputClass} cursor-pointer`}
              >
                <option value="">Select Relationship</option>
                <optgroup label="Immediate Family">
                  <option value="Spouse">Spouse</option>
                  <option value="Partner">Partner</option>
                </optgroup>
                <optgroup label="Children">
                  <option value="Child - Biological">Child - Biological</option>
                  <option value="Child - Adopted">Child - Adopted</option>
                  <option value="Child - Stepchild">Child - Stepchild</option>
                  <option value="Child - Legal Ward">Child - Legal Ward</option>
                </optgroup>
                <optgroup label="Parents">
                  <option value="Mother">Mother</option>
                  <option value="Father">Father</option>
                </optgroup>
                <optgroup label="In-Laws">
                  <option value="Family Unit">Family Unit</option>
                  <option value="Father-in-law">Father-in-law</option>
                  <option value="Mother-in-law">Mother-in-law</option>
                </optgroup>
                <optgroup label="Other">
                  <option value="Grandparent">Grandparent</option>
                  <option value="Grandchild">Grandchild</option>
                  <option value="Sibling">Sibling</option>
                  <option value="Other">Other</option>
                </optgroup>
              </select>
            </div>
          </div>

          {/* Financial & Beneficiary Status */}
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Financial Status</div>
            <div className={`col-span-9 ${inputCellClass} flex items-center gap-6`}>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={memberData.financiallySupported || false}
                  onChange={(e) => onChange({...memberData, financiallySupported: e.target.checked})}
                  className="accent-black"
                />
                <span className="text-sm font-serif">Financially Supported</span>
              </label>
              
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={memberData.isSupportive || false}
                  onChange={(e) => onChange({...memberData, isSupportive: e.target.checked})}
                  className="accent-black"
                />
                <span className="text-sm font-serif">Supportive (Contributes)</span>
              </label>
            </div>
          </div>

          {/* Beneficiary Section */}
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Beneficiary</div>
            <div className={`col-span-9 ${inputCellClass} flex items-center gap-4`}>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={memberData.isBeneficiary || false}
                  onChange={(e) => onChange({...memberData, isBeneficiary: e.target.checked})}
                  className="accent-black"
                />
                <span className="text-sm font-serif">Is Beneficiary</span>
              </label>

              {memberData.isBeneficiary && (
                <div className="flex items-center gap-2 ml-4 border-l border-gray-200 pl-4">
                   <span className="text-[10px] font-bold uppercase text-[#686a6c]">Percentage Split</span>
                   <div className="relative w-20">
                     <input
                       type="number"
                       min="0"
                       max="100"
                       value={memberData.beneficiaryPercentage || ''}
                       onChange={(e) => onChange({...memberData, beneficiaryPercentage: parseFloat(e.target.value)})}
                       className={`${inputClass} text-right pr-4`}
                       placeholder="0"
                     />
                     <span className="absolute right-0 top-1/2 -translate-y-1/2 text-sm text-gray-500">%</span>
                   </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* Row 3: ID / Passport */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>
          <div className="flex gap-4">
            <label className="flex items-center gap-1 cursor-pointer">
              <input 
                type="radio" 
                checked={isSACitizen} 
                onChange={() => {
                  setIsSACitizen(true);
                  if (memberData.passportNumber || memberData.countryOfBirth) {
                    onChange({ ...memberData, passportNumber: '', countryOfBirth: '' });
                  }
                }}
                className="accent-black"
              />
              SA ID
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input 
                type="radio" 
                checked={!isSACitizen} 
                onChange={() => {
                  setIsSACitizen(false);
                  if (memberData.idNumber) {
                    onChange({ ...memberData, idNumber: '' });
                  }
                }}
                className="accent-black"
              />
              Passport
            </label>
          </div>
        </div>
        
        {isSACitizen ? (
          <div className={`col-span-9 ${inputCellClass}`}>
            <input 
              type="text"
              value={memberData.idNumber}
              onChange={(e) => {
                const val = e.target.value.replace(/\D/g, '').slice(0, 13);
                onChange({...memberData, idNumber: val});
              }}
              className={`${inputClass} tracking-widest font-mono`}
              placeholder="0000000000000"
            />
          </div>
        ) : (
          <>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
              <input 
                type="text"
                value={memberData.passportNumber}
                onChange={(e) => onChange({...memberData, passportNumber: e.target.value})}
                className={inputClass}
                placeholder="Passport No."
              />
            </div>
            <div className={`col-span-2 ${labelCellClass}`}>Origin</div>
            <div className={`col-span-4 ${inputCellClass}`}>
              <input 
                type="text"
                value={memberData.countryOfBirth}
                onChange={(e) => onChange({...memberData, countryOfBirth: e.target.value})}
                className={inputClass}
                placeholder="Country of Birth"
              />
            </div>
          </>
        )}
      </div>

      {/* Derived Details Row */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-2 ${labelCellClass}`}>Gender</div>
        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
          {isSACitizen ? (
            <span className="text-sm font-serif">{memberData.gender || '-'}</span>
          ) : (
            <select
              value={memberData.gender}
              onChange={(e) => onChange({...memberData, gender: e.target.value})}
              className={inputClass}
            >
              <option value="">Select</option>
              <option value="Male">Male</option>
              <option value="Female">Female</option>
            </select>
          )}
        </div>

        <div className={`col-span-2 ${labelCellClass}`}>
          <div className="flex flex-col">
            <span>Date of Birth</span>
            <span className="text-[8px] text-gray-400 font-normal normal-case">(DD/MM/YYYY)</span>
          </div>
        </div>
        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <div className="flex flex-col justify-center h-full">
             <input 
               type="text"
               value={memberData.dateOfBirth || ''}
               onChange={(e) => onChange({...memberData, dateOfBirth: e.target.value})}
               className={inputClass}
               placeholder="DD/MM/YYYY"
             />
             {memberData.dobMonthAbbr && (
               <span className="text-[10px] text-blue-800 font-bold -mt-1">{memberData.dobMonthAbbr}</span>
             )}
           </div>
        </div>

        <div className={`col-span-2 ${labelCellClass}`}>Current Age</div>
        <div className={`col-span-2 ${inputCellClass}`}>
          <span className="text-sm font-serif">{memberData.currentAge || '-'}</span>
        </div>
      </div>

      {/* Birthday Calculation Row */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Age Next Birthday</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
          <span className="text-sm font-serif">{memberData.ageNextBirthday || '-'}</span>
        </div>

        <div className={`col-span-3 ${labelCellClass}`}>Days to Next Birthday</div>
        <div className={`col-span-3 ${inputCellClass}`}>
          <span className="text-sm font-serif">{memberData.daysToNextBirthday || '-'}</span>
        </div>
      </div>
    </div>
  );
};

export default MemberForm;
