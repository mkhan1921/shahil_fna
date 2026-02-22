import React from 'react';
import { EducationData } from '../types';

interface EducationSectionProps {
  data: EducationData;
  onChange: (data: EducationData) => void;
}

const EducationSection: React.FC<EducationSectionProps> = ({ data, onChange }) => {
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Education</h3>
      </div>
      
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Education Level</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <select
            value={data.level || ''}
            onChange={(e) => onChange({ ...data, level: e.target.value })}
            className={`${inputClass} cursor-pointer`}
          >
            <option value="">Select Level</option>
            <option value="High School">High School</option>
            <option value="Associate Degree">Associate Degree</option>
            <option value="Bachelor&apos;s Degree">Bachelor&apos;s Degree</option>
            <option value="Master&apos;s Degree">Master&apos;s Degree</option>
            <option value="PhD/JD/MD">PhD/JD/MD</option>
            <option value="Other">Other</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Institution</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <input
            type="text"
            value={data.institution || ''}
            onChange={(e) => onChange({ ...data, institution: e.target.value })}
            className={inputClass}
            placeholder="Name of Institution"
          />
        </div>
      </div>
    </div>
  );
};

export default EducationSection;
