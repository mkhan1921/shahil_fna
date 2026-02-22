import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { calculateTax } from '../utils/taxCalculations';
import { EmploymentData, IncomeSource, FutureIncome, EmploymentBreak } from '../types';

interface EmploymentSectionProps {
  data: EmploymentData;
  onChange: (data: EmploymentData) => void;
  age: number;
  medicalMembers: number;
}

const EmploymentSection: React.FC<EmploymentSectionProps> = ({ data, onChange, age, medicalMembers }) => {
  const [localData, setLocalData] = useState(data);

  // Sync local state with props only when props change
  useEffect(() => {
    setLocalData(data);
  }, [data]);

  // Optimized handleChange - batch updates
  const handleChange = useCallback(<K extends keyof EmploymentData>(field: K, value: EmploymentData[K]) => {
    setLocalData(prev => {
      const newData = { ...prev, [field]: value };
      // Only sync to parent for important fields
      if (field === 'primarySource' || field === 'paysTax' || field === 'medicalMembers') {
        onChange(newData);
      }
      return newData;
    });
  }, [onChange]);

  const handleBlur = useCallback(() => {
    onChange(localData);
  }, [localData, onChange]);

  // Calculations
  const calculateTotal = useCallback((sources: IncomeSource[]) => {
    return sources.reduce((sum, item) => sum + (parseFloat(item.amount) || 0), 0);
  }, []);

  const primaryIncome = parseFloat(localData.primaryIncomeAmount) || 0;
  const otherIncomeTotal = calculateTotal(localData.otherSources || []);
  const partnerIncomeTotal = calculateTotal(localData.partnerSources || []);

  const totalMonthlyIncome = primaryIncome + otherIncomeTotal + partnerIncomeTotal;
  const totalAnnualIncome = totalMonthlyIncome * 12;

  // Memoize tax calculation
  const taxInfo = useMemo(() => {
    const retirementAnnual = (parseFloat(localData.retirementContribution) || 0) * 12;
    const medMembers = localData.medicalMembers !== undefined ? localData.medicalMembers : medicalMembers;
    const taxableIncome = Math.max(0, totalAnnualIncome - retirementAnnual);
    return calculateTax(taxableIncome, age, medMembers);
  }, [totalAnnualIncome, age, medicalMembers, localData.retirementContribution, localData.medicalMembers]);

  const addSource = useCallback((type: 'other' | 'partner') => {
    setLocalData(prev => {
      const key = type === 'other' ? 'otherSources' : 'partnerSources';
      const current = prev[key] || [];
      const newData = { ...prev, [key]: [...current, { source: '', amount: '' }] };
      onChange(newData);
      return newData;
    });
  }, [onChange]);

  const updateSource = useCallback((type: 'other' | 'partner', index: number, field: keyof IncomeSource, value: string) => {
    setLocalData(prev => {
      const key = type === 'other' ? 'otherSources' : 'partnerSources';
      const current = [...(prev[key] || [])];
      current[index] = { ...current[index], [field]: value };
      return { ...prev, [key]: current };
    });
  }, []);

  const handleSourceBlur = useCallback(() => {
    onChange(localData);
  }, [localData, onChange]);

  const removeSource = useCallback((type: 'other' | 'partner', index: number) => {
    setLocalData(prev => {
      const key = type === 'other' ? 'otherSources' : 'partnerSources';
      const current = [...(prev[key] || [])];
      current.splice(index, 1);
      const newData = { ...prev, [key]: current };
      onChange(newData);
      return newData;
    });
  }, [onChange]);

  const updateCareerPlans = useCallback((field: keyof EmploymentData['careerPlans'], value: string) => {
    setLocalData(prev => {
      const currentPlans = prev.careerPlans || {};
      const newPlans = { ...currentPlans, [field]: value };
      const newData = { ...prev, careerPlans: newPlans };
      if (field === 'plannedChange') {
        onChange(newData);
      }
      return newData;
    });
  }, [onChange]);

  const addFutureIncome = useCallback(() => {
    setLocalData(prev => {
      const current = prev.futureIncome || [];
      const newData = { ...prev, futureIncome: [...current, { source: '', amount: '', startAge: '' }] };
      onChange(newData);
      return newData;
    });
  }, [onChange]);

  const updateFutureIncome = useCallback((index: number, field: keyof FutureIncome, value: string) => {
    setLocalData(prev => {
      const current = [...(prev.futureIncome || [])];
      current[index] = { ...current[index], [field]: value };
      const newData = { ...prev, futureIncome: current };
      if (field === 'source') {
        onChange(newData);
      }
      return newData;
    });
  }, [onChange]);

  const removeFutureIncome = useCallback((index: number) => {
    setLocalData(prev => {
      const current = [...(prev.futureIncome || [])];
      current.splice(index, 1);
      const newData = { ...prev, futureIncome: current };
      onChange(newData);
      return newData;
    });
  }, [onChange]);

  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const formatCurrency = useCallback((amount: number) => {
    if (!isMounted) return 'R 0.00';
    return amount.toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  }, [isMounted]);

  // Style constants
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Employment & Income</h3>
      </div>

      {/* Primary Employment Details */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Employment Status</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
          <select
            value={localData.employmentStatus || 'Full-time'}
            onChange={(e) => handleChange('employmentStatus', e.target.value)}
            className={`${inputClass} cursor-pointer`}
          >
            <option value="Full-time">Full-time</option>
            <option value="Part-time">Part-time</option>
            <option value="Self-employed">Self-employed</option>
            <option value="Student">Student</option>
            <option value="Retired">Retired</option>
            <option value="Unemployed">Unemployed</option>
          </select>
        </div>
        <div className={`col-span-2 ${labelCellClass}`}>Industry</div>
        <div className={`col-span-4 ${inputCellClass}`}>
           <input
            type="text"
            value={localData.industry || ''}
            onChange={(e) => handleChange('industry', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="e.g. Finance, Tech"
          />
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Job Title</div>
        <div className={`col-span-9 ${inputCellClass}`}>
           <input
            type="text"
            value={localData.jobTitle || localData.occupation || ''}
            onChange={(e) => {
                handleChange('jobTitle', e.target.value);
                handleChange('occupation', e.target.value); // Sync legacy field
            }}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="Current Job Title"
          />
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Salary Date</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <input
            type="text"
            value={localData.salaryDate || ''}
            onChange={(e) => handleChange('salaryDate', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="e.g. 25th"
          />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Debit Order Date</div>
        <div className={`col-span-3 ${inputCellClass}`}>
           <input
            type="text"
            value={localData.debitOrderDate || ''}
            onChange={(e) => handleChange('debitOrderDate', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="e.g. 1st"
          />
        </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Primary Source</div>
        <div className={`col-span-9 ${inputCellClass} border-r border-gray-200`}>
          <select
            value={localData.primarySource || ''}
            onChange={(e) => handleChange('primarySource', e.target.value)}
            className={`${inputClass} cursor-pointer`}
          >
            <option value="">Select Source</option>
            <option value="Employment">Employment</option>
            <option value="Self-Employment">Self-Employment</option>
            <option value="Rental Income">Rental Income</option>
            <option value="Investments">Investments</option>
            <option value="Pension">Pension</option>
            <option value="Other">Other</option>
          </select>
        </div>
      </div>

      {localData.primarySource === 'Employment' && (
        <>
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Employer Name</div>
            <div className={`col-span-9 ${inputCellClass}`}>
              <input
                type="text"
                value={localData.employer || ''}
                onChange={(e) => handleChange('employer', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Company Name"
              />
            </div>
          </div>
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Occupation</div>
            <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
              <input
                type="text"
                value={localData.occupation || ''}
                readOnly
                className={`${inputClass} text-gray-500`}
                placeholder="Job Title (Above)"
              />
            </div>
            <div className={`col-span-2 ${labelCellClass}`}>Tenure (Yrs)</div>
            <div className={`col-span-2 ${inputCellClass}`}>
              <input
                type="number"
                value={localData.tenure || ''}
                onChange={(e) => handleChange('tenure', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="0"
              />
            </div>
          </div>
        </>
      )}

      {/* Primary Income Amount */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Monthly Gross Income</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
          <input
            type="number"
            value={localData.primaryIncomeAmount || ''}
            onChange={(e) => handleChange('primaryIncomeAmount', e.target.value)}
            onBlur={handleBlur}
            className={inputClass}
            placeholder="0.00"
          />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Annual Gross</div>
        <div className={`col-span-3 ${inputCellClass}`}>
          <span className="text-sm font-serif">
            {formatCurrency(parseFloat(localData.primaryIncomeAmount || '0') * 12)}
          </span>
        </div>
      </div>

      {/* Tax Checkbox */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Tax Status</div>
        <div className={`col-span-9 ${inputCellClass} flex items-center gap-6 px-3 py-2`}>
           <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
             <input
               type="checkbox"
               checked={localData.paysTax || false}
               onChange={(e) => handleChange('paysTax', e.target.checked)}
               className="accent-black w-3 h-3"
             />
             <span className="text-xs font-bold uppercase text-[#686a6c]">Do you pay taxes on this?</span>
           </label>
           <div className="flex items-center gap-2">
             <span className="text-xs font-bold uppercase text-[#686a6c]">Total Years in Workforce:</span>
             <input
                type="number"
                value={localData.yearsInWorkforce || ''}
                onChange={(e) => handleChange('yearsInWorkforce', e.target.value)}
                onBlur={handleBlur}
                className="w-16 border-b border-gray-300 focus:border-black bg-transparent text-sm text-center"
                placeholder="0"
             />
           </div>
        </div>
      </div>

      {/* Employee Benefits */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Employee Benefits</div>
        <div className={`col-span-9 ${inputCellClass} px-3 py-2`}>
           <div className="flex items-center gap-2 mb-2">
             <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
               <input
                 type="checkbox"
                 checked={localData.hasEmployeeBenefits || false}
                 onChange={(e) => handleChange('hasEmployeeBenefits', e.target.checked)}
                 className="accent-black w-3 h-3"
               />
               <span className="text-xs font-bold uppercase text-[#686a6c]">Has Employee Benefits?</span>
             </label>
           </div>
           {localData.hasEmployeeBenefits && (
             <textarea
               value={localData.employeeBenefitsNotes || ''}
               onChange={(e) => handleChange('employeeBenefitsNotes', e.target.value)}
               onBlur={handleBlur}
               className={`${inputClass} border border-gray-200 rounded bg-gray-50 min-h-[60px] resize-none`}
               placeholder="Describe benefits (e.g. Medical Aid Subsidy 50%, Group Life 3x Annual Salary, Disability Cover, etc.)"
             />
           )}
        </div>
      </div>

      {/* Significant Employment Breaks */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 flex justify-between items-center border-b border-gray-200">
          <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Significant Employment Breaks (≥6 months)</span>
          <button type="button" onClick={() => {
            const current = localData.employmentBreaks || [];
            const newData = { ...localData, employmentBreaks: [...current, { reason: '', duration: '' }] };
            setLocalData(newData);
            onChange(newData);
          }} className="text-[10px] font-bold text-blue-800 uppercase hover:text-blue-600">+ Add Break</button>
        </div>
        {localData.employmentBreaks?.map((breakItem: EmploymentBreak, index: number) => (
          <div key={index} className="grid grid-cols-12 border-b border-gray-200 last:border-b-0">
            <div className={`col-span-2 ${labelCellClass}`}>Reason</div>
            <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
              <select
                value={breakItem.reason || ''}
                onChange={(e) => {
                  const current = [...(localData.employmentBreaks || [])];
                  current[index] = { ...current[index], reason: e.target.value };
                  const newData = { ...localData, employmentBreaks: current };
                  setLocalData(newData);
                  onChange(newData);
                }}
                className={inputClass}
              >
                <option value="">Select Reason</option>
                <option value="Parental Leave">Parental Leave</option>
                <option value="Health Issues">Health Issues</option>
                <option value="Education/Training">Education/Training</option>
                <option value="Unemployment">Unemployment</option>
                <option value="Sabbatical">Sabbatical</option>
                <option value="Other">Other</option>
              </select>
            </div>
            <div className={`col-span-2 ${labelCellClass}`}>Duration (Months)</div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
               <input
                type="number"
                value={breakItem.duration || ''}
                onChange={(e) => {
                  const current = [...(localData.employmentBreaks || [])];
                  current[index] = { ...current[index], duration: e.target.value };
                  const newData = { ...localData, employmentBreaks: current };
                  setLocalData(newData);
                }}
                onBlur={() => onChange(localData)}
                className={inputClass}
                placeholder="Months"
              />
            </div>
             <div className={`col-span-1 ${inputCellClass} flex justify-center items-center`}>
              <button type="button" onClick={() => {
                const current = [...(localData.employmentBreaks || [])];
                current.splice(index, 1);
                const newData = { ...localData, employmentBreaks: current };
                setLocalData(newData);
                onChange(newData);
              }} className="text-red-600 font-bold text-xs">×</button>
            </div>
          </div>
        ))}
      </div>

      {/* Other Income Sources */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 flex justify-between items-center border-b border-gray-200">
          <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Other Income Sources</span>
          <button type="button" onClick={() => addSource('other')} className="text-[10px] font-bold text-blue-800 uppercase hover:text-blue-600">+ Add Source</button>
        </div>
        {localData.otherSources?.map((source: IncomeSource, index: number) => (
          <div key={index} className="grid grid-cols-12 border-b border-gray-200 last:border-b-0">
            <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
              <input
                type="text"
                value={source.source}
                onChange={(e) => updateSource('other', index, 'source', e.target.value)}
                onBlur={handleSourceBlur}
                className={inputClass}
                placeholder="Source (e.g. Rental)"
              />
            </div>
            <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
               <input
                type="number"
                value={source.amount}
                onChange={(e) => updateSource('other', index, 'amount', e.target.value)}
                onBlur={handleSourceBlur}
                className={inputClass}
                placeholder="Amount/Month"
              />
            </div>
            <div className={`col-span-2 ${inputCellClass} flex justify-center items-center`}>
              <button type="button" onClick={() => removeSource('other', index)} className="text-red-600 font-bold text-xs">×</button>
            </div>
          </div>
        ))}
      </div>

      {/* Partner Income */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 flex justify-between items-center border-b border-gray-200">
          <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Partner Income</span>
          <button type="button" onClick={() => addSource('partner')} className="text-[10px] font-bold text-blue-800 uppercase hover:text-blue-600">+ Add Income</button>
        </div>
        {localData.partnerSources?.map((source: IncomeSource, index: number) => (
          <div key={index} className="grid grid-cols-12 border-b border-gray-200 last:border-b-0">
             <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
              <input
                type="text"
                value={source.source}
                onChange={(e) => updateSource('partner', index, 'source', e.target.value)}
                onBlur={handleSourceBlur}
                className={inputClass}
                placeholder="Partner Source"
              />
            </div>
            <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
               <input
                type="number"
                value={source.amount}
                onChange={(e) => updateSource('partner', index, 'amount', e.target.value)}
                onBlur={handleSourceBlur}
                className={inputClass}
                placeholder="Amount/Month"
              />
            </div>
             <div className={`col-span-2 ${inputCellClass} flex justify-center items-center`}>
              <button type="button" onClick={() => removeSource('partner', index)} className="text-red-600 font-bold text-xs">×</button>
            </div>
          </div>
        ))}
      </div>

      {/* Deductions & Credits */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 border-b border-gray-200">
           <h3 className="text-xs font-bold uppercase text-black tracking-wider">Tax Deductions & Credits</h3>
        </div>
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-4 ${labelCellClass}`}>Medical Aid Members</div>
          <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
             <input
                type="number"
                value={localData.medicalMembers !== undefined ? localData.medicalMembers : (medicalMembers || 0)}
                onChange={(e) => handleChange('medicalMembers', parseInt(e.target.value) || 0)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="0"
              />
          </div>
          <div className={`col-span-4 ${labelCellClass}`}>RA/Pension Contribution (Monthly)</div>
          <div className={`col-span-2 ${inputCellClass}`}>
             <input
                type="number"
                value={localData.retirementContribution || ''}
                onChange={(e) => handleChange('retirementContribution', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="0.00"
              />
          </div>
        </div>
      </div>

      {/* Totals & Tax */}
      <div className="border-b border-gray-200">
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-6 ${labelCellClass}`}>Combined Monthly Gross Income</div>
          <div className={`col-span-6 ${inputCellClass} font-bold`}>
            {formatCurrency(totalMonthlyIncome)}
          </div>
        </div>
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-6 ${labelCellClass}`}>Combined Annual Gross Income</div>
          <div className={`col-span-6 ${inputCellClass} font-bold`}>
            {formatCurrency(totalAnnualIncome)}
          </div>
        </div>
        
        {/* Tax Display */}
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-3 ${labelCellClass}`}>Estimated Tax</div>
          <div className={`col-span-3 ${inputCellClass} text-red-700`}>
             {formatCurrency(taxInfo.finalTax)}
          </div>
          <div className={`col-span-3 ${labelCellClass}`}>Net Annual Income</div>
          <div className={`col-span-3 ${inputCellClass} text-green-700 font-bold`}>
             {formatCurrency(taxInfo.netIncome)}
          </div>
        </div>
      </div>

      {/* Career Plans */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 border-b border-gray-200">
          <h3 className="text-xs font-bold uppercase text-black tracking-wider">Career Plans & Future Income</h3>
        </div>
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-3 ${labelCellClass}`}>Planned Changes</div>
          <div className={`col-span-9 ${inputCellClass} border-r border-gray-200`}>
            <select
              value={localData.careerPlans?.plannedChange || ''}
              onChange={(e) => updateCareerPlans('plannedChange', e.target.value)}
              className={inputClass}
            >
              <option value="">Select Plan</option>
              <option value="Promotion">Promotion</option>
              <option value="Career Switch">Career Switch</option>
              <option value="Entrepreneurship">Entrepreneurship</option>
              <option value="Part-Time Work">Part-Time Work</option>
              <option value="Other">Other</option>
            </select>
          </div>
        </div>
        <div className="grid grid-cols-12 border-b border-gray-200">
           <div className={`col-span-3 ${labelCellClass}`}>New Potential Salary</div>
           <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input
                type="number"
                value={localData.careerPlans?.potentialSalary || ''}
                onChange={(e) => updateCareerPlans('potentialSalary', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Amount"
              />
           </div>
           <div className={`col-span-2 ${labelCellClass}`}>Notes</div>
           <div className={`col-span-4 ${inputCellClass}`}>
             <input
                type="text"
                value={localData.careerPlans?.notes || ''}
                onChange={(e) => updateCareerPlans('notes', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Mini notes..."
              />
           </div>
        </div>
      </div>

      {/* Anticipated Future Income */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 flex justify-between items-center border-b border-gray-200">
          <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Anticipated Future Income</span>
          <button type="button" onClick={addFutureIncome} className="text-[10px] font-bold text-blue-800 uppercase hover:text-blue-600">+ Add Future Income</button>
        </div>
        {localData.futureIncome?.map((item: FutureIncome, index: number) => (
          <div key={index} className="grid grid-cols-12 border-b border-gray-200 last:border-b-0">
             <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
               <select
                value={item.source || ''}
                onChange={(e) => updateFutureIncome(index, 'source', e.target.value)}
                className={inputClass}
              >
                <option value="">Select Source</option>
                <option value="Pension/Retirement income">Pension/Retirement income</option>
                <option value="Rental Property">Rental Property</option>
                <option value="Investments">Investments</option>
                <option value="Inheritance">Inheritance</option>
                <option value="Part-Time Work">Part-Time Work</option>
                <option value="Other">Other</option>
              </select>
            </div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
               <input
                type="number"
                value={item.amount || ''}
                onChange={(e) => updateFutureIncome(index, 'amount', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Annual Gross Amount"
              />
            </div>
            <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
               <input
                type="number"
                value={item.startAge || ''}
                onChange={(e) => updateFutureIncome(index, 'startAge', e.target.value)}
                onBlur={handleBlur}
                className={inputClass}
                placeholder="Start Year/Age"
              />
            </div>
            <div className={`col-span-1 ${inputCellClass} flex justify-center items-center`}>
              <button type="button" onClick={() => removeFutureIncome(index)} className="text-red-600 font-bold text-xs">×</button>
            </div>
          </div>
        ))}
      </div>
      
    </div>
  );
};

export default EmploymentSection;
