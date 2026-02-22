'use client';

import React, { useState, useEffect } from 'react';
import { FinancialPlanningData } from '../types';

interface FinancialPlanningSectionProps {
  data: FinancialPlanningData;
  onChange: (data: FinancialPlanningData) => void;
}

const FinancialPlanningSection: React.FC<FinancialPlanningSectionProps> = ({ data, onChange }) => {
  // Local state to prevent race conditions during fast typing
  const [localData, setLocalData] = useState(data);

  // Sync local state when props change (e.g. from other sections or initial load)
  useEffect(() => {
    setLocalData(data);
  }, [data]);

  const handleChange = <K extends keyof FinancialPlanningData>(field: K, value: FinancialPlanningData[K]) => {
    const newData = { ...localData, [field]: value };
    setLocalData(newData);
    // For checkboxes/selects, we can sync immediately as they are not high-frequency
    if (typeof value === 'boolean' || field === 'advisorType') {
        onChange(newData);
    }
  };

  const handleBlur = () => {
    onChange(localData);
  };

  // Shared classes matching the design system
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const checkboxLabelClass = "flex items-center gap-2 cursor-pointer text-xs font-bold uppercase text-[#686a6c] px-2 py-1 hover:bg-gray-100 rounded";
  const checkboxClass = "accent-black w-3 h-3";

  const updateAccountantService = (service: 'taxPlanning' | 'investmentManagement', value: boolean) => {
    const newData = {
      ...localData,
      accountantServices: {
        ...localData.accountantServices,
        [service]: value
      }
    };
    setLocalData(newData);
    onChange(newData);
  };

  const updateAttorneyService = (service: 'estatePlanning', value: boolean) => {
    const newData = {
      ...localData,
      attorneyServices: {
        ...localData.attorneyServices,
        [service]: value
      }
    };
    setLocalData(newData);
    onChange(newData);
  };

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Topics to Address & Financial History</h3>
      </div>

      {/* Topics to Address */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Preferred Insurers/Funds</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <input
            type="text"
            value={localData.preferredInsurers || ''}
            onChange={(e) => handleChange('preferredInsurers', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="e.g. Allan Gray, Discovery"
          />
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Disliked Insurers/Funds</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <input
            type="text"
            value={localData.dislikedInsurers || ''}
            onChange={(e) => handleChange('dislikedInsurers', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="e.g. Old Mutual"
          />
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Plans to Adjust/Cancel</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <textarea
            value={localData.plannedChanges || ''}
            onChange={(e) => handleChange('plannedChanges', e.target.value)}
            onBlur={handleBlur}
            className={`${inputClass} resize-none`}
            rows={2}
            placeholder="Any specific plans to change existing policies..."
          />
        </div>
      </div>

      {/* Financial Advisor History */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Previous Financial Advisor?</div>
        <div className={`col-span-9 ${inputCellClass} flex items-center gap-6 px-3 py-2 px-3 py-2 px-3 py-2`}>
          <label className={checkboxLabelClass}>
            <input
              type="checkbox"
              checked={localData.hasFinancialAdvisor}
              onChange={(e) => handleChange('hasFinancialAdvisor', e.target.checked)}
              className={checkboxClass}
            />
            Yes
          </label>
        </div>
      </div>

      {localData.hasFinancialAdvisor && (
        <>
          <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50/50">
            <div className={`col-span-3 ${labelCellClass}`}>Advisor Details</div>
            <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
              <input
                type="text"
                value={localData.advisorName || ''}
                onChange={(e) => handleChange('advisorName', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Advisor Name / Firm"
              />
            </div>
            <div className={`col-span-2 ${labelCellClass}`}>Relationship Dynamic</div>
            <div className={`col-span-3 ${inputCellClass}`}>
              <input
                type="text"
                value={localData.advisorDynamic || ''}
                onChange={(e) => handleChange('advisorDynamic', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="e.g. Active, Dormant"
              />
            </div>
          </div>

          <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50/50">
            <div className={`col-span-3 ${labelCellClass}`}>Advisor Type</div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
              <select
                value={localData.advisorType || ''}
                onChange={(e) => handleChange('advisorType', e.target.value as FinancialPlanningData['advisorType'])}
                className={inputClass}
              >
                <option value="">Select Type...</option>
                <option value="Broker">Broker</option>
                <option value="Tied Agent">Tied Agent</option>
              </select>
            </div>
            <div className={`col-span-3 ${labelCellClass}`}>Last Review Date</div>
            <div className={`col-span-3 ${inputCellClass}`}>
              <input
                type="date"
                value={localData.lastReviewDate || ''}
                onChange={(e) => handleChange('lastReviewDate', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
              />
            </div>
          </div>

          <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50/50">
            <div className={`col-span-3 ${labelCellClass}`}>Last Contact Date</div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
              <input
                type="date"
                value={localData.lastContactDate || ''}
                onChange={(e) => handleChange('lastContactDate', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
              />
            </div>
            <div className={`col-span-3 ${labelCellClass}`}>Reason for Discontinuation</div>
            <div className={`col-span-3 ${inputCellClass}`}>
              <input
                type="text"
                value={localData.discontinuationReason || ''}
                onChange={(e) => handleChange('discontinuationReason', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Reason..."
              />
            </div>
          </div>
        </>
      )}

      {/* Professional Relationships */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Do you have an Accountant?</div>
        <div className={`col-span-9 ${inputCellClass} flex items-center gap-6 px-3 py-2`}>
          <label className={checkboxLabelClass}>
            <input
              type="checkbox"
              checked={localData.hasAccountant}
              onChange={(e) => handleChange('hasAccountant', e.target.checked)}
              className={checkboxClass}
            />
            Yes
          </label>
          
          {localData.hasAccountant && (
            <div className="flex gap-4 ml-4 pl-4 border-l border-gray-300">
              <span className="text-[10px] text-gray-400 font-serif">Do they cover:</span>
              <label className={checkboxLabelClass}>
                <input
                  type="checkbox"
                  checked={localData.accountantServices?.taxPlanning || false}
                  onChange={(e) => updateAccountantService('taxPlanning', e.target.checked)}
                  className={checkboxClass}
                />
                Tax Planning
              </label>
              <label className={checkboxLabelClass}>
                <input
                  type="checkbox"
                  checked={localData.accountantServices?.investmentManagement || false}
                  onChange={(e) => updateAccountantService('investmentManagement', e.target.checked)}
                  className={checkboxClass}
                />
                Investment Management
              </label>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Do you have an Attorney?</div>
        <div className={`col-span-9 ${inputCellClass} flex items-center gap-6`}>
          <label className={checkboxLabelClass}>
            <input
              type="checkbox"
              checked={localData.hasAttorney}
              onChange={(e) => handleChange('hasAttorney', e.target.checked)}
              className={checkboxClass}
            />
            Yes
          </label>
          
          {localData.hasAttorney && (
            <div className="flex gap-4 ml-4 pl-4 border-l border-gray-300">
              <span className="text-[10px] text-gray-400 font-serif">Do they cover:</span>
              <label className={checkboxLabelClass}>
                <input
                  type="checkbox"
                  checked={localData.attorneyServices?.estatePlanning || false}
                  onChange={(e) => updateAttorneyService('estatePlanning', e.target.checked)}
                  className={checkboxClass}
                />
                Estate Planning
              </label>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default FinancialPlanningSection;
