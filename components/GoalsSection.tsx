
'use client';

import React from 'react';
import { Goal, EmergencyFund } from '../types';

interface GoalsSectionProps {
  goals: Goal[];
  emergencyFund: EmergencyFund;
  currentAge: number;
  onGoalsChange: (goals: Goal[]) => void;
  onEmergencyFundChange: (emergencyFund: EmergencyFund) => void;
}

const calculateFutureCost = (currentCost: number, years: number) => {
  return currentCost * Math.pow(1.07, years);
};

const calculateMonthlyContribution = (targetAmount: number, currentSaved: number, months: number) => {
  if (months <= 0) return 0;
  const required = targetAmount - currentSaved;
  return Math.max(0, required / months);
};

const calculatePMT = (principal: number, annualRate: number, months: number) => {
  if (months <= 0 || annualRate <= 0) return 0;
  const r = annualRate / 100 / 12;
  return (principal * r * Math.pow(1 + r, months)) / (Math.pow(1 + r, months) - 1);
};

const GoalsSection: React.FC<GoalsSectionProps> = ({ 
  goals, 
  emergencyFund, 
  currentAge,
  onGoalsChange, 
  onEmergencyFundChange 
}) => {
  // Helper for currency formatting
  const formatCurrency = (val: string | number) => {
    const num = typeof val === 'string' ? parseFloat(val) : val;
    if (isNaN(num)) return 'R 0.00';
    return new Intl.NumberFormat('en-ZA', { style: 'currency', currency: 'ZAR' }).format(num);
  };

  const handleAddGoal = () => {
    const newGoal: Goal = {
      id: crypto.randomUUID(),
      category: 'Other',
      name: '',
      type: 'Short-Term',
      currentCost: '0',
      financingRequired: false
    };
    onGoalsChange([...goals, newGoal]);
  };

  const handleRemoveGoal = (id: string) => {
    onGoalsChange(goals.filter(g => g.id !== id));
  };

  const updateGoal = (id: string, field: keyof Goal, value: string | number | boolean) => {
    onGoalsChange(goals.map(g => {
      if (g.id === id) {
        return { ...g, [field]: value };
      }
      return g;
    }));
  };

  // Shared styles
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const selectClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black font-serif appearance-none cursor-pointer px-3 py-2";
  const sectionHeaderClass = "px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-100">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Goal Details & Investments</h3>
      </div>

      {/* Emergency Fund Section */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 bg-gray-50 border-b border-gray-200">
           <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Emergency Fund & Preparedness</span>
        </div>
        
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-3 ${labelCellClass}`}>Target Months of Expenses</div>
          <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input
              type="number"
              value={emergencyFund?.targetMonths || ''}
              onChange={(e) => onEmergencyFundChange({...emergencyFund, targetMonths: e.target.value})}
              className={inputClass}
              placeholder="e.g. 3 or 6"
            />
          </div>
          <div className={`col-span-3 ${labelCellClass}`}>Current Emergency Savings</div>
          <div className={`col-span-3 ${inputCellClass}`}>
            <input
              type="number"
              value={emergencyFund?.currentSavings || ''}
              onChange={(e) => onEmergencyFundChange({...emergencyFund, currentSavings: e.target.value})}
              className={inputClass}
              placeholder="R 0.00"
            />
          </div>
        </div>
        
        <div className="grid grid-cols-12 border-b border-gray-200">
          <div className={`col-span-3 ${labelCellClass}`}>Monthly Contribution</div>
          <div className={`col-span-9 ${inputCellClass}`}>
            <input
              type="number"
              value={emergencyFund?.monthlyContribution || ''}
              onChange={(e) => onEmergencyFundChange({...emergencyFund, monthlyContribution: e.target.value})}
              className={inputClass}
              placeholder="R 0.00"
            />
          </div>
        </div>
      </div>

      {/* Goals List */}
      <div>
        <div className="px-3 py-2 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
            <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Goals</span>
            <button 
               type="button" 
               onClick={handleAddGoal}
               className="text-[#0000cc] text-[10px] font-bold uppercase tracking-wider hover:underline"
             >
               + Add New Goal
             </button>
        </div>

        {goals.map((goal, index) => {
          // Derived values for display
          const currentCost = parseFloat(goal.currentCost) || 0;
          let yearsToGoal = 0;
          
          if (goal.targetDate) {
            const target = new Date(goal.targetDate);
            const now = new Date();
            yearsToGoal = (target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24 * 365.25);
          } else if (goal.targetAge && currentAge) {
            yearsToGoal = parseFloat(goal.targetAge) - currentAge;
          }
          
          yearsToGoal = Math.max(0, yearsToGoal);
          
          const futureCost = calculateFutureCost(currentCost, yearsToGoal);
          const monthsToGoal = yearsToGoal * 12;
          const lumpSumSaved = parseFloat(goal.lumpSumSaved || '0');
          const requiredMonthly = calculateMonthlyContribution(futureCost, lumpSumSaved, monthsToGoal);

          // Loan Calc
          const deposit = parseFloat(goal.depositAmount || '0');
          const loanAmount = futureCost - deposit;
          const loanTermMonths = parseFloat(goal.loanTerm || '0');
          const interestRate = parseFloat(goal.interestRate || '0');
          const monthlyRepayment = calculatePMT(loanAmount, interestRate, loanTermMonths);

          return (
            <div key={goal.id} className="border-b-4 border-gray-100 last:border-b-0">
              <div className={sectionHeaderClass}>
                <span className="text-[10px] font-bold uppercase text-blue-900">Goal #{index + 1}</span>
                <button
                  onClick={() => handleRemoveGoal(goal.id)}
                  className="text-red-600 text-[10px] font-bold uppercase hover:text-red-800"
                >
                  Remove
                </button>
              </div>

              {/* Basic Details */}
              <div className="grid grid-cols-12 border-b border-gray-200">
                <div className={`col-span-2 ${labelCellClass}`}>Category</div>
                <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <select
                    value={goal.category}
                    onChange={(e) => updateGoal(goal.id, 'category', e.target.value)}
                    className={selectClass}
                  >
                    <option value="Education">Education</option>
                    <option value="Car">Car</option>
                    <option value="Family Support">Family Support</option>
                    <option value="Home Purchase">Home Purchase</option>
                    <option value="Business">Business</option>
                    <option value="Other">Other</option>
                  </select>
                </div>
                <div className={`col-span-2 ${labelCellClass}`}>Goal Name</div>
                <div className={`col-span-4 ${inputCellClass}`}>
                  <input
                    type="text"
                    value={goal.name}
                    onChange={(e) => updateGoal(goal.id, 'name', e.target.value)}
                    className={inputClass}
                    placeholder="e.g. Daughter's University"
                  />
                </div>
              </div>

              <div className="grid grid-cols-12 border-b border-gray-200">
                <div className={`col-span-2 ${labelCellClass}`}>Type</div>
                <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <select
                    value={goal.type}
                    onChange={(e) => updateGoal(goal.id, 'type', e.target.value)}
                    className={selectClass}
                  >
                    <option value="Short-Term">Short-Term (1–3 yrs)</option>
                    <option value="Mid-Term">Mid-Term (3–7 yrs)</option>
                    <option value="Long-Term">Long-Term (7+ yrs)</option>
                  </select>
                </div>
                <div className={`col-span-2 ${labelCellClass}`}>Target Date</div>
                <div className={`col-span-4 ${inputCellClass} flex gap-2`}>
                   <input
                    type="date"
                    value={goal.targetDate || ''}
                    onChange={(e) => updateGoal(goal.id, 'targetDate', e.target.value)}
                    className={`${inputClass} flex-1`}
                  />
                  <input
                    type="number"
                    value={goal.targetAge || ''}
                    onChange={(e) => updateGoal(goal.id, 'targetAge', e.target.value)}
                    className={`${inputClass} w-20 text-center border-l border-gray-200 pl-2`}
                    placeholder={currentAge ? `Age: ${currentAge}` : 'Age'}
                  />
                </div>
              </div>

              {/* Financials */}
              <div className="grid grid-cols-12 border-b border-gray-200">
                <div className={`col-span-2 ${labelCellClass}`}>Today&apos;s Cost</div>
                <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                  <input
                    type="number"
                    value={goal.currentCost}
                    onChange={(e) => updateGoal(goal.id, 'currentCost', e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className={`col-span-2 ${labelCellClass}`}>Est. Future Cost</div>
                <div className={`col-span-2 ${inputCellClass} border-r border-gray-200 bg-gray-50/50 flex items-center px-3 py-2`}>
                   <span className="text-sm text-gray-700 font-serif">{formatCurrency(futureCost)}</span>
                </div>
                <div className={`col-span-2 ${labelCellClass}`}>Lump Sum Saved</div>
                <div className={`col-span-2 ${inputCellClass}`}>
                  <input
                    type="number"
                    value={goal.lumpSumSaved || ''}
                    onChange={(e) => updateGoal(goal.id, 'lumpSumSaved', e.target.value)}
                    className={inputClass}
                    placeholder="R 0.00"
                  />
                </div>
              </div>

              <div className="grid grid-cols-12 border-b border-gray-200">
                 <div className={`col-span-3 ${labelCellClass}`}>Current Monthly Contrib.</div>
                 <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                    <input
                      type="number"
                      value={goal.monthlyContributionCurrent || ''}
                      onChange={(e) => updateGoal(goal.id, 'monthlyContributionCurrent', e.target.value)}
                      className={inputClass}
                      placeholder="R 0.00"
                    />
                 </div>
                 <div className={`col-span-3 ${labelCellClass}`}>Required Savings (Monthly)</div>
                 <div className={`col-span-3 ${inputCellClass} bg-gray-50/50 flex items-center`}>
                    {!goal.financingRequired && (
                        <span className="text-sm font-bold text-blue-800 font-serif">{formatCurrency(requiredMonthly)}</span>
                    )}
                 </div>
              </div>

              {/* Financing Section */}
              <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50/30">
                 <div className={`col-span-3 ${labelCellClass}`}>Financing Required?</div>
                 <div className={`col-span-9 ${inputCellClass} flex items-center px-3 py-2`}>
                    <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                      <input
                        type="checkbox"
                        checked={goal.financingRequired}
                        onChange={(e) => updateGoal(goal.id, 'financingRequired', e.target.checked)}
                        className="accent-black w-3 h-3"
                      />
                      <span className="text-[10px] font-bold uppercase text-[#686a6c]">Yes</span>
                    </label>
                 </div>
              </div>

              {goal.financingRequired && (
                <>
                  <div className="grid grid-cols-12 border-b border-gray-200 bg-orange-50/20">
                    <div className={`col-span-3 ${labelCellClass}`}>Save Deposit Only?</div>
                    <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                       <select
                          value={goal.saveDepositOnly ? 'Yes' : 'No'}
                          onChange={(e) => updateGoal(goal.id, 'saveDepositOnly', e.target.value === 'Yes')}
                          className={selectClass}
                        >
                          <option value="No">No (Full Cost)</option>
                          <option value="Yes">Yes</option>
                        </select>
                    </div>
                    <div className={`col-span-3 ${labelCellClass}`}>Deposit Amount</div>
                    <div className={`col-span-3 ${inputCellClass}`}>
                      <input
                        type="number"
                        value={goal.depositAmount || ''}
                        onChange={(e) => updateGoal(goal.id, 'depositAmount', e.target.value)}
                        className={inputClass}
                        placeholder="R 0.00"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-12 border-b border-gray-200 bg-orange-50/20">
                    <div className={`col-span-3 ${labelCellClass}`}>Loan Term (Months)</div>
                    <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                      <input
                          type="number"
                          value={goal.loanTerm || ''}
                          onChange={(e) => updateGoal(goal.id, 'loanTerm', e.target.value)}
                          className={inputClass}
                          placeholder="e.g. 60"
                        />
                    </div>
                    <div className={`col-span-3 ${labelCellClass}`}>Interest Rate (%)</div>
                    <div className={`col-span-3 ${inputCellClass}`}>
                       <input
                          type="number"
                          value={goal.interestRate || ''}
                          onChange={(e) => updateGoal(goal.id, 'interestRate', e.target.value)}
                          className={inputClass}
                          placeholder="e.g. 11.75"
                        />
                    </div>
                  </div>

                  <div className="grid grid-cols-12 border-b border-gray-200 bg-orange-50/40">
                     <div className={`col-span-3 ${labelCellClass}`}>Est. Monthly Repayment</div>
                     <div className={`col-span-3 ${inputCellClass} border-r border-gray-200 flex items-center`}>
                        <span className="text-sm font-medium text-gray-800 font-serif">{formatCurrency(monthlyRepayment)}</span>
                     </div>
                     <div className={`col-span-3 ${labelCellClass}`}>Required Savings (Monthly)</div>
                     <div className={`col-span-3 ${inputCellClass} flex items-center px-3 py-2`}>
                        <span className="text-sm font-bold text-blue-800 font-serif">
                            {formatCurrency(
                                calculateMonthlyContribution(
                                    (goal.financingRequired && goal.saveDepositOnly) ? deposit : futureCost, 
                                    lumpSumSaved, 
                                    monthsToGoal
                                )
                            )}
                        </span>
                     </div>
                  </div>
                </>
              )}

              <div className="grid grid-cols-12 border-b border-gray-200">
                <div className={`col-span-3 ${labelCellClass}`}>Ongoing Expenses (After Purchase)</div>
                <div className={`col-span-9 ${inputCellClass}`}>
                  <input
                    type="number"
                    value={goal.ongoingExpenses || ''}
                    onChange={(e) => updateGoal(goal.id, 'ongoingExpenses', e.target.value)}
                    className={inputClass}
                    placeholder="e.g. Insurance, Maintenance"
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default GoalsSection;
