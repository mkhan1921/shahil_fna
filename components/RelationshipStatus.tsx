'use client';

import React from 'react';
import { RelationshipStatusData } from '../types';

interface RelationshipStatusProps {
  data: RelationshipStatusData;
  onChange: (data: RelationshipStatusData) => void;
}

const RelationshipStatus: React.FC<RelationshipStatusProps> = ({ data, onChange }) => {
  const { maritalStatus } = data;

  const isPartnered = ['Married', 'Domestic Partnership'].includes(maritalStatus);


  // Shared classes
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "px-3 py-2 flex items-center";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black font-serif";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Relationship & Marital Status</h3>
      </div>
      
      <div className="grid grid-cols-12">
        <div className={`col-span-2 ${labelCellClass} border-b border-gray-200`}>
          Marital Status
        </div>
        <div className={`col-span-10 ${inputCellClass} border-b border-gray-200`}>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {['Single', 'Married', 'Divorced', 'Widowed', 'Separated', 'Domestic Partnership'].map((status) => (
              <label key={status} className="flex items-center gap-2 cursor-pointer group">
                <div className={`w-3 h-3 border border-gray-300 rounded-full flex items-center justify-center transition-colors ${maritalStatus === status ? 'border-black' : 'group-hover:border-gray-400'}`}>
                  {maritalStatus === status && <div className="w-1.5 h-1.5 bg-black rounded-full" />}
                </div>
                <input 
                  type="radio"
                  name="maritalStatus"
                  value={status}
                  checked={maritalStatus === status}
                  onChange={(e) => onChange({...data, maritalStatus: e.target.value})}
                  className="hidden"
                />
                <span className={`text-[10px] uppercase tracking-wider ${maritalStatus === status ? 'text-black font-bold' : 'text-gray-500'}`}>
                  {status}
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>

      {isPartnered && (
        <div className="grid grid-cols-12 border-t border-gray-200">
          <div className={`col-span-2 ${labelCellClass}`}>
            {maritalStatus === 'Married' ? 'Marriage Date' : 'Partnership Date'}
          </div>
          <div className={`col-span-10 ${inputCellClass}`}>
            <input 
              type="date"
              value={data.partnershipDate || ''}
              onChange={(e) => onChange({...data, partnershipDate: e.target.value})}
              className={inputClass}
            />
          </div>
        </div>
      )}
    </div>
  );
};

export default RelationshipStatus;