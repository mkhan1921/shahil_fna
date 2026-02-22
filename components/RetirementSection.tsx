import React, { useCallback } from 'react';
import { RetirementData, RetirementFund, RetirementIncome, Asset, Liability } from '../types';

interface RetirementSectionProps {
  data: RetirementData;
  onChange: (data: RetirementData) => void;
  currentAge: number;
  monthlySalary: number;
  assets: Asset[];
  liabilities: Liability[];
}

const RetirementSection: React.FC<RetirementSectionProps> = ({ data, onChange, currentAge, monthlySalary, assets, liabilities }) => {
  const formatCurrency = useCallback((amount: number) => {
    const num = typeof amount === 'string' ? parseFloat(amount) : amount;
    return (num || 0).toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  }, []);

  const updateData = useCallback((updates: Partial<RetirementData>) => {
    onChange({ ...data, ...updates });
  }, [data, onChange]);

  // --- Fund Management ---
  const addFund = useCallback(() => {
    const newFund: RetirementFund = {
      id: Math.random().toString(36).substr(2, 9),
      companyName: '',
      productName: '',
      currentBalance: '',
      growthRate: ''
    };
    updateData({ retirementFunds: [...(data.retirementFunds || []), newFund] });
  }, [data.retirementFunds, updateData]);

  const updateFund = useCallback((index: number, field: keyof RetirementFund, value: string) => {
    const newFunds = [...(data.retirementFunds || [])];
    newFunds[index] = { ...newFunds[index], [field]: value };
    updateData({ retirementFunds: newFunds });
  }, [data.retirementFunds, updateData]);

  const removeFund = useCallback((index: number) => {
    const newFunds = [...(data.retirementFunds || [])];
    newFunds.splice(index, 1);
    updateData({ retirementFunds: newFunds });
  }, [data.retirementFunds, updateData]);

  // --- Income Source Management ---
  const addIncomeSource = useCallback((type: 'other' | 'annuity') => {
    const newItem: RetirementIncome = {
      id: Math.random().toString(36).substr(2, 9),
      source: '',
      monthlyAmount: ''
    };
    if (type === 'other') {
      updateData({ otherIncomeSources: [...(data.otherIncomeSources || []), newItem] });
    } else {
      updateData({ activeAnnuities: [...(data.activeAnnuities || []), newItem] });
    }
  }, [data.otherIncomeSources, data.activeAnnuities, updateData]);

  const updateIncomeSource = useCallback((type: 'other' | 'annuity', index: number, field: keyof RetirementIncome, value: string) => {
    const key = type === 'other' ? 'otherIncomeSources' : 'activeAnnuities';
    const list = [...(data[key] || [])];
    list[index] = { ...list[index], [field]: value };
    updateData({ [key]: list });
  }, [data, updateData]);

  const removeIncomeSource = useCallback((type: 'other' | 'annuity', index: number) => {
    const key = type === 'other' ? 'otherIncomeSources' : 'activeAnnuities';
    const list = [...(data[key] || [])];
    list.splice(index, 1);
    updateData({ [key]: list });
  }, [data, updateData]);

  // --- Strategies Management ---
  const toggleLiability = (id: string) => {
    const current = data.payoffLiabilities || [];
    const updated = current.includes(id) 
      ? current.filter(lid => lid !== id)
      : [...current, id];
    updateData({ payoffLiabilities: updated });
  };

  const toggleAssetSell = (id: string) => {
    const current = data.assetsToSell || [];
    const updated = current.includes(id)
      ? current.filter(aid => aid !== id)
      : [...current, id];
    updateData({ assetsToSell: updated });
  };

  // --- Calculations ---
  const totalSavings = (data.retirementFunds || []).reduce((sum, f) => sum + (parseFloat(f.currentBalance) || 0), 0);
  
  // Liquidity Adjustments
  const liabilitiesPayoffAmount = (data.payoffLiabilities || []).reduce((sum, id) => {
      const liability = liabilities.find(l => l.id === id);
      return sum + (liability ? parseFloat(liability.currentBalance) || 0 : 0);
  }, 0);

  const assetsSellAmount = (data.assetsToSell || []).reduce((sum, id) => {
      const asset = assets.find(a => a.id === id);
      return sum + (asset ? parseFloat(asset.currentValue || asset.currentBalance || '0') || 0 : 0);
  }, 0);

  const adjustedCapital = Math.max(0, totalSavings - liabilitiesPayoffAmount + assetsSellAmount);

  // Contributions
  const empMatch = parseFloat(data.employerMatchPercentage || '0') || 0;
  const empContrib = parseFloat(data.employerContribution || '0') || (monthlySalary * (empMatch / 100));
  const ownContrib = parseFloat(data.employeeContribution || '0') || 0;
  const totalMonthlyContrib = empContrib + ownContrib;

  // Projections
  const desiredAge = parseFloat(data.desiredRetirementAge || '65');
  const yearsToRetirement = Math.max(0, desiredAge - currentAge);
  
  // Earliest Retirement Age Calculation (Simulation Loop)
  const calculateEarliestRetirementAge = () => {
    const targetIncome = parseFloat(data.targetMonthlyIncome || '0');
    if (targetIncome <= 0) return null;

    // Assumptions
    const withdrawalRateMonthly = 0.008; // 9.6% annual
    const nominalAnnualReturn = 0.10;
    const monthlyReturn = nominalAnnualReturn / 12;
    
    // Growth rates for contributions
    const empContribIncrease = (parseFloat(data.employerContributionIncrease || '0') || 0) / 100;
    const ownContribIncrease = (parseFloat(data.employeeContributionIncrease || '0') || 0) / 100;

    let currentSimCapital = adjustedCapital;
    let currentEmpContrib = empContrib;
    let currentOwnContrib = ownContrib;
    let months = 0;
    const maxMonths = 1200; // 100 years limit

    // Target Capital (Static for now, could be inflated if we want Real Terms)
    // Assuming Target Income is "Today's Money" and we want to beat inflation, we should use Real Return.
    // However, existing logic uses Nominal Return. Let's stick to Nominal for consistency with existing "Aggressive" stance.
    const targetCorpus = targetIncome / withdrawalRateMonthly;

    while (months < maxMonths) {
        // Check if funded
        if (currentSimCapital >= targetCorpus) {
            return currentAge + (months / 12);
        }

        // Apply Growth
        currentSimCapital = currentSimCapital * (1 + monthlyReturn);

        // Add Contributions
        currentSimCapital += (currentEmpContrib + currentOwnContrib);

        months++;

        // Annual Increases
        if (months % 12 === 0) {
            currentEmpContrib *= (1 + empContribIncrease);
            currentOwnContrib *= (1 + ownContribIncrease);
        }
    }
    
    return null; // Never reached
  };

  const earliestAge = calculateEarliestRetirementAge();

  // Tax Rules (2025/2026 Tables)
  // 1/3 Lump Sum
  const oneThirdLumpSum = adjustedCapital / 3;
  const twoThirdsAnnuity = adjustedCapital * (2/3);
  
  // Tax on Lump Sum (Retirement Table)
  // 0 - 550k: 0%
  // 550k - 770k: 18%
  // 770k - 1155k: 27% + 39600
  // 1155k+: 36% + 143550
  let taxOnLumpSum = 0;
  
  if (oneThirdLumpSum > 1155000) {
      taxOnLumpSum = 143550 + ((oneThirdLumpSum - 1155000) * 0.36);
  } else if (oneThirdLumpSum > 770000) {
      taxOnLumpSum = 39600 + ((oneThirdLumpSum - 770000) * 0.27);
  } else if (oneThirdLumpSum > 550000) {
      taxOnLumpSum = (oneThirdLumpSum - 550000) * 0.18;
  }

  // Potential Income from 2/3 Annuity
  const potentialMonthlyAnnuityIncome = twoThirdsAnnuity * 0.005; // Conservative 6% PA / 12

  // Shared Styles
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const sectionHeaderClass = "px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center";

  return (
    <div className="border-b border-gray-200 mt-8">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-100">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Retirement Planning</h3>
      </div>

      {/* Summary / Dashboard */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-green-50/50">
         <div className="col-span-3 p-3 text-center border-r border-gray-200">
            <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Savings</div>
            <div className="text-sm font-bold text-green-900">{formatCurrency(totalSavings)}</div>
         </div>
         <div className="col-span-3 p-3 text-center border-r border-gray-200">
            <div className="text-[10px] font-bold uppercase text-[#686a6c]">Adj. Capital</div>
            <div className="text-sm font-bold text-green-900" title="After Liab. Payoff & Asset Sales">{formatCurrency(adjustedCapital)}</div>
         </div>
         <div className="col-span-3 p-3 text-center border-r border-gray-200">
            <div className="text-[10px] font-bold uppercase text-[#686a6c]">Monthly Contrib.</div>
            <div className="text-sm font-bold text-green-900">{formatCurrency(totalMonthlyContrib)}</div>
         </div>
         <div className="col-span-3 p-3 text-center">
            <div className="text-[10px] font-bold uppercase text-[#686a6c]">Earliest Age</div>
            <div className="text-sm font-bold text-blue-900">
                {earliestAge ? `${earliestAge.toFixed(1)} Years` : 'N/A'}
            </div>
         </div>
      </div>

      {/* 1. Retirement Savings */}
      <div className="border-b border-gray-200">
         <div className={sectionHeaderClass}>
            <span className="text-[10px] font-bold uppercase text-black">Retirement Accounts</span>
            <div className="flex gap-4">
                <span className="text-[8px] uppercase text-gray-500 flex items-center gap-1">
                    <input type="checkbox" disabled className="w-3 h-3" /> = Emp. Benefit
                </span>
                <button type="button" onClick={addFund} className="text-[#0000cc] text-[10px] font-bold uppercase hover:underline">+ Add Fund</button>
            </div>
         </div>
         {(data.retirementFunds || []).map((fund, index) => (
            <div key={fund.id} className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                  <input type="text" value={fund.companyName} onChange={(e) => updateFund(index, 'companyName', e.target.value)} className={inputClass} placeholder="Company (e.g. Allan Gray)" />
               </div>
               <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                  <input type="text" value={fund.productName} onChange={(e) => updateFund(index, 'productName', e.target.value)} className={inputClass} placeholder="Product (e.g. RA)" />
               </div>
               <div className={`col-span-1 ${inputCellClass} border-r border-gray-200 flex items-center justify-center`}>
                  <input type="checkbox" checked={fund.isEmployeeBenefit === 'true'} onChange={(e) => updateFund(index, 'isEmployeeBenefit', e.target.checked.toString())} className="accent-black w-3 h-3" title="Employee Benefit?" />
               </div>
               <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                  <input type="number" value={fund.currentBalance} onChange={(e) => updateFund(index, 'currentBalance', e.target.value)} className={inputClass} placeholder="Balance" />
               </div>
               <div className={`col-span-2 ${inputCellClass} border-r border-gray-200 flex items-center`}>
                  <input type="number" value={fund.growthRate} onChange={(e) => updateFund(index, 'growthRate', e.target.value)} className={inputClass} placeholder="Growth %" />
                  <span className="text-xs">%</span>
               </div>
               <div className="col-span-1 flex items-center justify-center">
                  <button type="button" onClick={() => removeFund(index)} className="text-red-600 font-bold text-xs">×</button>
               </div>
            </div>
         ))}
      </div>

      {/* 2. Contributions */}
      <div className={sectionHeaderClass}>
         <span className="text-[10px] font-bold uppercase text-black">Contribution Increase Plan</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Employer Match %</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={data.employerMatchPercentage || ''} onChange={(e) => updateData({ employerMatchPercentage: e.target.value })} className={inputClass} placeholder="0%" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Employer Amount</div>
         <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={data.employerContribution || (empMatch > 0 ? empContrib.toFixed(2) : '')} onChange={(e) => updateData({ employerContribution: e.target.value })} className={inputClass} placeholder="0.00" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Employer Annual Inc. %</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={data.employerContributionIncrease || ''} onChange={(e) => updateData({ employerContributionIncrease: e.target.value })} className={inputClass} placeholder="e.g. 5%" />
         </div>
         <div className={`col-span-6 ${labelCellClass} bg-gray-50 text-gray-400 italic`}>
             Expected increase in employer contributions
         </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Employee Contribution</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={data.employeeContribution || ''} onChange={(e) => updateData({ employeeContribution: e.target.value })} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Employee Annual Inc. %</div>
         <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={data.employeeContributionIncrease || ''} onChange={(e) => updateData({ employeeContributionIncrease: e.target.value })} className={inputClass} placeholder="e.g. 10%" />
         </div>
      </div>

      {/* 3. Strategies (Liabilities & Assets) */}
      <div className={sectionHeaderClass}>
         <span className="text-[10px] font-bold uppercase text-black">Strategies at Retirement</span>
         <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold uppercase text-[#686a6c]">Optimization:</span>
            <select
                value={data.optimizationStrategy || ''}
                onChange={(e) => updateData({ optimizationStrategy: e.target.value as RetirementData['optimizationStrategy'] })}
                className="text-[10px] border border-gray-200 rounded px-1 py-0.5 bg-white"
            >
                <option value="">None</option>
                <option value="Reliable Income">Reliable Income</option>
                <option value="Max Drawdown">Max Drawdown</option>
                <option value="Tax Efficient">Tax Efficient</option>
            </select>
         </div>
      </div>
      
      {/* Liabilities to Payoff */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-red-50/30">
          <div className="col-span-12 px-3 py-2 border-b border-gray-200">
              <span className="text-[10px] font-bold uppercase text-[#686a6c]">Payoff Liabilities (Reduces Capital)</span>
          </div>
          {liabilities.length === 0 && <div className="col-span-12 px-3 py-2 text-xs text-gray-400 italic">No liabilities recorded</div>}
          {liabilities.map(l => (
              <div key={l.id} className="col-span-12 grid grid-cols-12 px-3 py-1 hover:bg-red-50">
                  <div className="col-span-1">
                      <input 
                        type="checkbox" 
                        checked={(data.payoffLiabilities || []).includes(l.id)} 
                        onChange={() => toggleLiability(l.id)}
                      />
                  </div>
                  <div className="col-span-8 text-xs">{l.name} ({l.type})</div>
                  <div className="col-span-3 text-xs font-bold text-right">{formatCurrency(parseFloat(l.currentBalance))}</div>
              </div>
          ))}
          <div className="col-span-12 px-3 py-2 border-t border-gray-200 flex justify-between">
              <span className="text-xs font-bold">Total Payoff Amount:</span>
              <span className="text-xs font-bold text-red-600">-{formatCurrency(liabilitiesPayoffAmount)}</span>
          </div>
      </div>

      {/* Assets to Sell */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-green-50/30">
          <div className="col-span-12 px-3 py-2 border-b border-gray-200">
              <span className="text-[10px] font-bold uppercase text-[#686a6c]">Sell Assets (Increases Capital)</span>
          </div>
          {assets.length === 0 && <div className="col-span-12 px-3 py-2 text-xs text-gray-400 italic">No assets recorded</div>}
          {assets.map(a => (
              <div key={a.id} className="col-span-12 grid grid-cols-12 px-3 py-1 hover:bg-green-50">
                  <div className="col-span-1">
                      <input 
                        type="checkbox" 
                        checked={(data.assetsToSell || []).includes(a.id)} 
                        onChange={() => toggleAssetSell(a.id)}
                      />
                  </div>
                  <div className="col-span-8 text-xs">{a.name} ({a.category})</div>
                  <div className="col-span-3 text-xs font-bold text-right">{formatCurrency(parseFloat(a.currentValue || a.currentBalance || '0'))}</div>
              </div>
          ))}
          <div className="col-span-12 px-3 py-2 border-t border-gray-200 flex justify-between">
              <span className="text-xs font-bold">Total Sale Proceeds:</span>
              <span className="text-xs font-bold text-green-600">+{formatCurrency(assetsSellAmount)}</span>
          </div>
      </div>


      {/* 4. Projections & Goals */}
      <div className={sectionHeaderClass}>
         <span className="text-[10px] font-bold uppercase text-black">Goals & Projections</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Desired Ret. Age</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={data.desiredRetirementAge || ''} onChange={(e) => updateData({ desiredRetirementAge: e.target.value })} className={inputClass} placeholder="e.g. 65" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Time Until</div>
         <div className={`col-span-3 ${inputCellClass} text-xs flex items-center`}>
            {yearsToRetirement > 0 ? `${Math.floor(yearsToRetirement)} Years` : '-'}
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Target Monthly Income</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={data.targetMonthlyIncome || ''} onChange={(e) => updateData({ targetMonthlyIncome: e.target.value })} className={inputClass} placeholder="Today's Value" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Simultaneous Ret.?</div>
         <div className={`col-span-3 ${inputCellClass} flex items-center px-3 py-2`}>
            <label className="flex items-center gap-1 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
              <input type="checkbox" checked={data.simultaneousRetirement || false} onChange={(e) => updateData({ simultaneousRetirement: e.target.checked })} className="accent-black w-3 h-3" />
              <span className="text-xs font-bold uppercase text-[#686a6c]">Yes</span>
            </label>
         </div>
      </div>

      {/* 5. Analysis (1/3 vs 2/3) */}
      <div className={sectionHeaderClass}>
         <span className="text-[10px] font-bold uppercase text-black">Analysis (Adjusted Capital)</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200 bg-yellow-50/30">
         <div className={`col-span-3 ${labelCellClass}`}>1/3 Cash Lump Sum</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            {formatCurrency(oneThirdLumpSum)}
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Tax on Lump Sum</div>
         <div className={`col-span-3 ${inputCellClass} text-red-600 font-bold`}>
            {formatCurrency(taxOnLumpSum)}
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200 bg-yellow-50/30">
         <div className={`col-span-3 ${labelCellClass}`}>2/3 Compulsory Annuity</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            {formatCurrency(twoThirdsAnnuity)}
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Est. Monthly Income</div>
         <div className={`col-span-3 ${inputCellClass} text-green-600 font-bold`}>
            {formatCurrency(potentialMonthlyAnnuityIncome)}
         </div>
      </div>

      {/* 6. Other Income */}
      <div className="border-b border-gray-200">
         <div className={sectionHeaderClass}>
            <span className="text-[10px] font-bold uppercase text-black">Other Income Sources</span>
            <button type="button" onClick={() => addIncomeSource('other')} className="text-[#0000cc] text-[10px] font-bold uppercase hover:underline">+ Add</button>
         </div>
         {(data.otherIncomeSources || []).map((item, index) => (
            <div key={item.id} className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <select value={item.source} onChange={(e) => updateIncomeSource('other', index, 'source', e.target.value)} className={`${inputClass} cursor-pointer`}>
                     <option value="">Select Source...</option>
                     <option value="Rental Property">Rental Property</option>
                     <option value="Social Security">Social Security</option>
                     <option value="Part-Time Work">Part-Time Work</option>
                     <option value="Inheritance">Inheritance</option>
                     <option value="Other">Other</option>
                  </select>
               </div>
               <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <input type="number" value={item.monthlyAmount} onChange={(e) => updateIncomeSource('other', index, 'monthlyAmount', e.target.value)} className={inputClass} placeholder="Monthly Amount" />
               </div>
               <div className="col-span-4 flex items-center px-3">
                  <button type="button" onClick={() => removeIncomeSource('other', index)} className="text-red-600 font-bold text-xs">Remove</button>
               </div>
            </div>
         ))}
      </div>

      {/* 7. Active Annuities */}
      <div className="border-b border-gray-200">
         <div className={sectionHeaderClass}>
            <span className="text-[10px] font-bold uppercase text-black">Active Annuities (Drawing)</span>
            <button type="button" onClick={() => addIncomeSource('annuity')} className="text-[#0000cc] text-[10px] font-bold uppercase hover:underline">+ Add</button>
         </div>
         {(data.activeAnnuities || []).map((item, index) => (
            <div key={item.id} className="grid grid-cols-12 border-b border-gray-200">
               <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <input type="text" value={item.source} onChange={(e) => updateIncomeSource('annuity', index, 'source', e.target.value)} className={inputClass} placeholder="Provider / Policy" />
               </div>
               <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                  <input type="number" value={item.monthlyAmount} onChange={(e) => updateIncomeSource('annuity', index, 'monthlyAmount', e.target.value)} className={inputClass} placeholder="Monthly Draw" />
               </div>
               <div className="col-span-4 flex items-center px-3">
                  <button type="button" onClick={() => removeIncomeSource('annuity', index)} className="text-red-600 font-bold text-xs">Remove</button>
               </div>
            </div>
         ))}
      </div>

    </div>
  );
};

export default RetirementSection;
