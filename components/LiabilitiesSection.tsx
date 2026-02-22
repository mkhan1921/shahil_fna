
import React, { useState } from 'react';
import { Liability, Asset } from '../types';

interface LiabilitiesSectionProps {
  liabilities: Liability[];
  assets: Asset[];
  onChange: (liabilities: Liability[]) => void;
}

const LiabilitiesSection: React.FC<LiabilitiesSectionProps> = ({ liabilities, assets, onChange }) => {
  const [isMounted, setIsMounted] = useState(false);

  React.useEffect(() => {
    setIsMounted(true);
  }, []);

  const formatCurrency = (val: number) => {
    if (!isMounted) return 'R 0.00';
    return val.toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  };
  
  const addLiability = (type: Liability['type']) => {
    const newLiability: Liability = {
      id: Math.random().toString(36).substr(2, 9),
      type,
      name: '',
      isSecured: false,
      currentBalance: '',
      interestRate: '',
      monthlyPayment: '',
      status: 'Current',
      earlyPayoff: false,
      termUnit: 'Years'
    };
    onChange([...liabilities, newLiability]);
  };

  const updateLiability = (id: string, updates: Partial<Liability>) => {
    onChange(liabilities.map(l => l.id === id ? { ...l, ...updates } : l));
  };

  const removeLiability = (id: string) => {
    onChange(liabilities.filter(l => l.id !== id));
  };

  // Calculations
  const totalBalance = liabilities.reduce((sum, l) => sum + (parseFloat(l.currentBalance) || 0), 0);
  const totalMonthlyPayment = liabilities.reduce((sum, l) => sum + (parseFloat(l.monthlyPayment) || 0), 0);
  
  // Estimate payoff time (simple interest assumption for quick calc)
  const monthsToPayoff = totalMonthlyPayment > 0 ? Math.ceil(totalBalance / totalMonthlyPayment) : 0;
  const yearsToPayoff = Math.floor(monthsToPayoff / 12);
  const remainingMonths = monthsToPayoff % 12;

  // Shared styles
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const sectionHeaderClass = "px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center";

  return (
    <div className="border-b border-gray-200 mt-8">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-100">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Liabilities & Debt</h3>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-red-50/50">
        <div className="col-span-4 p-3 text-center border-r border-gray-200">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Outstanding</div>
           <div className="text-sm font-bold text-red-900">{formatCurrency(totalBalance)}</div>
        </div>
        <div className="col-span-4 p-3 text-center border-r border-gray-200">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Monthly Repayment</div>
           <div className="text-sm font-bold text-red-900">{formatCurrency(totalMonthlyPayment)}</div>
        </div>
        <div className="col-span-4 p-3 text-center">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Est. Payoff Time</div>
           <div className="text-sm font-bold text-black">
             {monthsToPayoff > 0 ? `${yearsToPayoff} Years, ${remainingMonths} Months` : '-'}
           </div>
        </div>
      </div>

      {/* List */}
      <div>
        {liabilities.map((liability, index) => (
          <div key={liability.id} className="border-b-4 border-gray-100 last:border-b-0">
            <div className={sectionHeaderClass}>
              <span className="text-[10px] font-bold uppercase text-red-900">{liability.type} #{index + 1}</span>
              <button type="button" onClick={() => removeLiability(liability.id)} className="text-red-600 text-[10px] font-bold uppercase hover:text-red-800">Remove</button>
            </div>

            {/* Common Fields */}
            <div className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-3 ${labelCellClass}`}>
                 {liability.type === 'Loan' ? 'Lender / Name' : 'Name'}
               </div>
               <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
                 <input 
                   type="text" 
                   value={liability.name} 
                   onChange={(e) => updateLiability(liability.id, { name: e.target.value })} 
                   className={inputClass} 
                   placeholder="e.g. Nedbank Home Loan"
                 />
               </div>
               <div className={`col-span-2 ${labelCellClass}`}>Type</div>
               <div className={`col-span-2 ${inputCellClass} flex items-center`}>
                  <select 
                    value={liability.isSecured ? 'Secured' : 'Unsecured'} 
                    onChange={(e) => updateLiability(liability.id, { isSecured: e.target.value === 'Secured' })}
                    className={`${inputClass} cursor-pointer`}
                  >
                    <option value="Unsecured">Unsecured</option>
                    <option value="Secured">Secured</option>
                  </select>
               </div>
            </div>

            {/* Loan Specifics */}
            {liability.type === 'Loan' && (
              <>
                 <div className="grid grid-cols-12 border-b border-gray-200">
                    <div className={`col-span-3 ${labelCellClass}`}>Linked Asset</div>
                    <div className={`col-span-9 ${inputCellClass}`}>
                       <select 
                         value={liability.linkedAssetId || ''} 
                         onChange={(e) => updateLiability(liability.id, { linkedAssetId: e.target.value })}
                         className={`${inputClass} cursor-pointer`}
                       >
                         <option value="">None / Unlinked</option>
                         {assets.map(a => (
                           <option key={a.id} value={a.id}>{a.name} ({a.type})</option>
                         ))}
                       </select>
                    </div>
                 </div>
                 <div className="grid grid-cols-12 border-b border-gray-200">
                    <div className={`col-span-3 ${labelCellClass}`}>Original Amount</div>
                    <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                      <input 
                        type="number" 
                        value={liability.originalAmount || ''} 
                        onChange={(e) => updateLiability(liability.id, { originalAmount: e.target.value })} 
                        className={inputClass} 
                        placeholder="0.00"
                      />
                    </div>
                    <div className={`col-span-2 ${labelCellClass}`}>Term</div>
                    <div className={`col-span-4 ${inputCellClass} flex gap-2`}>
                      <input 
                        type="number" 
                        value={liability.term || ''} 
                        onChange={(e) => updateLiability(liability.id, { term: e.target.value })} 
                        className="w-16 border-b border-gray-300 focus:border-black outline-none text-sm font-serif" 
                        placeholder="0"
                      />
                      <select 
                        value={liability.termUnit || 'Years'} 
                        onChange={(e) => updateLiability(liability.id, { termUnit: e.target.value as Liability['termUnit'] })}
                        className="text-xs bg-transparent border-none focus:ring-0 cursor-pointer"
                      >
                        <option value="Years">Years</option>
                        <option value="Months">Months</option>
                      </select>
                    </div>
                 </div>
              </>
            )}

            {/* Financials */}
            <div className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-3 ${labelCellClass}`}>Current Balance</div>
               <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                  <input 
                    type="number" 
                    value={liability.currentBalance || ''} 
                    onChange={(e) => updateLiability(liability.id, { currentBalance: e.target.value })} 
                    className={inputClass} 
                    placeholder="0.00"
                  />
               </div>
               <div className={`col-span-3 ${labelCellClass}`}>Interest Rate (APR %)</div>
               <div className={`col-span-3 ${inputCellClass}`}>
                  <input 
                    type="number" 
                    value={liability.interestRate || ''} 
                    onChange={(e) => updateLiability(liability.id, { interestRate: e.target.value })} 
                    className={inputClass} 
                    placeholder="0.0"
                  />
               </div>
            </div>

            <div className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-3 ${labelCellClass}`}>Monthly Payment</div>
               <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                  <input 
                    type="number" 
                    value={liability.monthlyPayment || ''} 
                    onChange={(e) => updateLiability(liability.id, { monthlyPayment: e.target.value })} 
                    className={inputClass} 
                    placeholder="0.00"
                  />
               </div>
               <div className={`col-span-3 ${labelCellClass}`}>Status</div>
               <div className={`col-span-3 ${inputCellClass}`}>
                  <select 
                    value={liability.status || 'Current'} 
                    onChange={(e) => updateLiability(liability.id, { status: e.target.value as Liability['status'] })}
                    className={`${inputClass} cursor-pointer`}
                  >
                    <option value="Current">Current</option>
                    <option value="Delinquent">Delinquent</option>
                  </select>
               </div>
            </div>

            {/* Options */}
            <div className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-3 ${labelCellClass}`}>Early Payoff Plan?</div>
               <div className={`col-span-3 ${inputCellClass} border-r border-gray-200 flex items-center px-3 py-2`}>
                  <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                    <input type="checkbox" checked={!!liability.earlyPayoff} onChange={(e) => updateLiability(liability.id, { earlyPayoff: e.target.checked })} className="accent-black w-3 h-3" />
                    <span className="text-xs uppercase font-bold text-[#686a6c]">Yes</span>
                  </label>
               </div>
               
               {/* Student Loan Specifics */}
               {liability.type === 'Other' && liability.name.toLowerCase().includes('student') && (
                 <>
                    <div className={`col-span-3 ${labelCellClass}`}>Target Free Date</div>
                    <div className={`col-span-3 ${inputCellClass}`}>
                       <input 
                         type="date" 
                         value={liability.targetPayoffDate || ''} 
                         onChange={(e) => updateLiability(liability.id, { targetPayoffDate: e.target.value })} 
                         className={inputClass} 
                       />
                    </div>
                 </>
               )}
            </div>
            
            {/* Consolidation for Student Loans */}
             {liability.type === 'Other' && liability.name.toLowerCase().includes('student') && (
               <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-4 ${labelCellClass}`}>Consolidation Considered?</div>
                  <div className={`col-span-8 ${inputCellClass}`}>
                     <select 
                       value={liability.isConsolidated || 'No'} 
                       onChange={(e) => updateLiability(liability.id, { isConsolidated: e.target.value })}
                       className={`${inputClass} cursor-pointer`}
                     >
                       <option value="No">No</option>
                       <option value="Yes">Yes</option>
                       <option value="Already Consolidated">Already Consolidated</option>
                     </select>
                  </div>
               </div>
             )}

          </div>
        ))}
      </div>

      {/* Add Buttons */}
      <div className="p-3 bg-gray-50 flex gap-2">
        <button type="button" onClick={() => addLiability('Loan')} className="px-3 py-1 bg-white border border-gray-300 text-[10px] font-bold uppercase hover:bg-gray-100">+ Add Loan</button>
        <button type="button" onClick={() => addLiability('Credit Card')} className="px-3 py-1 bg-white border border-gray-300 text-[10px] font-bold uppercase hover:bg-gray-100">+ Add Credit Card</button>
        <button type="button" onClick={() => addLiability('Other')} className="px-3 py-1 bg-white border border-gray-300 text-[10px] font-bold uppercase hover:bg-gray-100">+ Add Other Liability</button>
      </div>

    </div>
  );
};

export default LiabilitiesSection;
