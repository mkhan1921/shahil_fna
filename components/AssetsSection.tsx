
import React, { useCallback, useMemo } from 'react';
import { Asset } from '../types';

interface AssetsSectionProps {
  assets: Asset[];
  onChange: (assets: Asset[]) => void;
}

const AssetsSection: React.FC<AssetsSectionProps> = ({ assets, onChange }) => {
  const formatCurrency = useCallback((amount: number) => {
    const num = typeof amount === 'string' ? parseFloat(amount) : amount;
    return (num || 0).toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  }, []);

  // Helper to add new asset
  const addAsset = useCallback((category: 'Physical' | 'Financial', type: string = '') => {
    const newAsset: Asset = {
      id: Math.random().toString(36).substr(2, 9),
      name: '',
      type: type,
      category,
      isFinanced: false,
      planToSell: false,
      sellAtRetirement: 'Unsure',
      settleDebtOnDeath: false
    };
    onChange([...assets, newAsset]);
  }, [assets, onChange]);

  const updateAsset = useCallback((id: string, updates: Partial<Asset>) => {
    onChange(assets.map(a => a.id === id ? { ...a, ...updates } : a));
  }, [assets, onChange]);

  const removeAsset = useCallback((id: string) => {
    onChange(assets.filter(a => a.id !== id));
  }, [assets, onChange]);

  const calculateDebtTime = useCallback((debt: string, repayment: string) => {
    const d = parseFloat(debt) || 0;
    const r = parseFloat(repayment) || 0;
    if (d <= 0 || r <= 0) return { months: 0, years: 0, totalMonths: 0 };
    const totalMonths = Math.ceil(d / r);
    const years = Math.floor(totalMonths / 12);
    const months = totalMonths % 12;
    return { months, years, totalMonths };
  }, []);

  // Shared styles
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const sectionHeaderClass = "px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center";

  // Financial Asset Types
  const financialTypes = useMemo(() => [
    'TFSA', 'RA', 'Endowment', 'Private Business Inv', 'Cryptocurrency',
    'Bank Account', 'Stocks', 'Bonds', 'Money Market', 'Cash'
  ], []);

  // Memoize calculations
  const totalPhysical = useMemo(() => 
    assets.filter(a => a.category === 'Physical')
      .reduce((sum, a) => sum + (parseFloat(a.currentValue || '0') || 0), 0),
    [assets]
  );

  const totalFinancial = useMemo(() => 
    assets.filter(a => a.category === 'Financial')
      .reduce((sum, a) => sum + (parseFloat(a.currentBalance || '0') || 0), 0),
    [assets]
  );

  const totalAssets = totalPhysical + totalFinancial;

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-100">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Assets & Investments</h3>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-blue-50/50">
        <div className="col-span-4 p-3 text-center border-r border-gray-200">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Physical Assets</div>
           <div className="text-sm font-bold text-blue-900">{formatCurrency(totalPhysical)}</div>
        </div>
        <div className="col-span-4 p-3 text-center border-r border-gray-200">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Financial Assets</div>
           <div className="text-sm font-bold text-blue-900">{formatCurrency(totalFinancial)}</div>
        </div>
        <div className="col-span-4 p-3 text-center">
           <div className="text-[10px] font-bold uppercase text-[#686a6c]">Total Assets</div>
           <div className="text-sm font-bold text-black">{formatCurrency(totalAssets)}</div>
        </div>
      </div>

      {/* PHYSICAL ASSETS SECTION */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
            <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Physical Assets (Property, Vehicles, etc.)</span>
            <button 
               type="button" 
               onClick={() => addAsset('Physical')}
               className="text-[#0000cc] text-[10px] font-bold uppercase tracking-wider hover:underline"
             >
               + Add Physical Asset
             </button>
        </div>
        
        <div>
          {assets.filter(a => a.category === 'Physical').map((asset, index) => {
             const debtTime = calculateDebtTime(asset.remainingDebt || '0', asset.monthlyRepayment || '0');
             
             return (
              <div key={asset.id} className="border-b-4 border-gray-100 last:border-b-0">
                <div className={sectionHeaderClass}>
                  <span className="text-[10px] font-bold uppercase text-blue-900">Physical Asset #{index + 1}</span>
                  <button type="button" onClick={() => removeAsset(asset.id)} className="text-red-600 text-[10px] font-bold uppercase hover:text-red-800">Remove</button>
                </div>

                {/* Name & Type */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-2 ${labelCellClass}`}>Asset Name</div>
                  <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="text" 
                      value={asset.name} 
                      onChange={(e) => updateAsset(asset.id, { name: e.target.value })} 
                      className={inputClass} 
                      placeholder="e.g. Beach House"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Type</div>
                  <div className={`col-span-4 ${inputCellClass}`}>
                    <select 
                      value={asset.type} 
                      onChange={(e) => updateAsset(asset.id, { type: e.target.value })}
                      className={`${inputClass} cursor-pointer`}
                    >
                      <option value="">Select Type</option>
                      <option value="Real Estate">Real Estate</option>
                      <option value="Vehicle">Vehicle</option>
                      <option value="Investment Account">Investment Account</option>
                      <option value="Business">Business</option>
                      <option value="Primary Residence">Primary Residence</option>
                      <option value="Rental Property">Rental Property</option>
                      <option value="Vacation Home">Vacation Home</option>
                      <option value="Other">Other</option>
                    </select>
                  </div>
                </div>

                {/* Location & Purchase Info */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-2 ${labelCellClass}`}>Location</div>
                  <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="text" 
                      value={asset.location || ''} 
                      onChange={(e) => updateAsset(asset.id, { location: e.target.value })} 
                      className={inputClass} 
                      placeholder="City/Area"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Purchase Date</div>
                  <div className={`col-span-4 ${inputCellClass}`}>
                    <input 
                      type="date" 
                      value={asset.purchaseDate || ''} 
                      onChange={(e) => updateAsset(asset.id, { purchaseDate: e.target.value })} 
                      className={inputClass} 
                    />
                  </div>
                </div>

                {/* Values */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-2 ${labelCellClass}`}>Purchase Price</div>
                  <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="number" 
                      value={asset.purchasePrice || ''} 
                      onChange={(e) => updateAsset(asset.id, { purchasePrice: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Current Value</div>
                  <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="number" 
                      value={asset.currentValue || ''} 
                      onChange={(e) => updateAsset(asset.id, { currentValue: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Growth %</div>
                  <div className={`col-span-2 ${inputCellClass}`}>
                    <input 
                      type="number" 
                      value={asset.appreciationRate || ''} 
                      onChange={(e) => updateAsset(asset.id, { appreciationRate: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.0"
                    />
                  </div>
                </div>

                {/* Financing */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-2 ${labelCellClass}`}>Status</div>
                  <div className={`col-span-4 ${inputCellClass} border-r border-gray-200 flex gap-2`}>
                    <label className="flex items-center gap-1 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                      <input type="radio" checked={!asset.isFinanced} onChange={() => updateAsset(asset.id, { isFinanced: false })} className="accent-black" />
                      <span className="text-xs">Owned</span>
                    </label>
                    <label className="flex items-center gap-1 cursor-pointer">
                      <input type="radio" checked={!!asset.isFinanced} onChange={() => updateAsset(asset.id, { isFinanced: true })} className="accent-black" />
                      <span className="text-xs">Financed</span>
                    </label>
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Ownership</div>
                  <div className={`col-span-4 ${inputCellClass}`}>
                     <select 
                      value={asset.ownershipType || ''} 
                      onChange={(e) => updateAsset(asset.id, { ownershipType: e.target.value })}
                      className={`${inputClass} cursor-pointer`}
                    >
                      <option value="">Select...</option>
                      <option value="Personal">Personal</option>
                      <option value="Trust">Trust</option>
                      <option value="Joint">Joint</option>
                      <option value="Company">Company</option>
                    </select>
                  </div>
                </div>

                {/* Dynamic Financing Fields */}
                {asset.isFinanced && (
                  <div className="bg-red-50/30">
                     <div className="grid grid-cols-12 border-b border-gray-200">
                        <div className={`col-span-2 ${labelCellClass}`}>Amount Paid</div>
                        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                          <input 
                            type="number" 
                            value={asset.amountPaid || ''} 
                            onChange={(e) => updateAsset(asset.id, { amountPaid: e.target.value })} 
                            className={inputClass} 
                            placeholder="0.00"
                          />
                        </div>
                        <div className={`col-span-2 ${labelCellClass}`}>Remaining Debt</div>
                        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                          <input 
                            type="number" 
                            value={asset.remainingDebt || ''} 
                            onChange={(e) => updateAsset(asset.id, { remainingDebt: e.target.value })} 
                            className={inputClass} 
                            placeholder="0.00"
                          />
                        </div>
                        <div className={`col-span-2 ${labelCellClass}`}>Monthly Repayment</div>
                        <div className={`col-span-2 ${inputCellClass}`}>
                          <input 
                            type="number" 
                            value={asset.monthlyRepayment || ''} 
                            onChange={(e) => updateAsset(asset.id, { monthlyRepayment: e.target.value })} 
                            className={inputClass} 
                            placeholder="0.00"
                          />
                        </div>
                     </div>
                     <div className="grid grid-cols-12 border-b border-gray-200 bg-red-50/50">
                        <div className={`col-span-2 ${labelCellClass}`}>Time Remaining</div>
                        <div className="col-span-10 px-3 py-2 text-xs font-serif flex items-center gap-4">
                           {debtTime.totalMonths > 0 ? (
                             <>
                               <span className="font-bold">{debtTime.years} Years, {debtTime.months} Months</span>
                               <span className="text-gray-500">({debtTime.totalMonths} total months)</span>
                             </>
                           ) : (
                             <span className="text-gray-400 italic">Enter debt and repayment to calculate</span>
                           )}
                        </div>
                     </div>
                     <div className="grid grid-cols-12 border-b border-gray-200">
                       <div className={`col-span-3 ${labelCellClass}`}>Settle on Early Death?</div>
                       <div className={`col-span-9 ${inputCellClass} flex items-center px-3 py-2`}>
                          <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                            <input type="checkbox" checked={!!asset.settleDebtOnDeath} onChange={(e) => updateAsset(asset.id, { settleDebtOnDeath: e.target.checked })} className="accent-black w-3 h-3" />
                            <span className="text-xs uppercase font-bold text-[#686a6c]">Yes</span>
                          </label>
                       </div>
                     </div>
                  </div>
                )}

                {/* Income & Cost */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-3 ${labelCellClass}`}>Monthly Income Generated</div>
                  <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="number" 
                      value={asset.monthlyIncome || ''} 
                      onChange={(e) => updateAsset(asset.id, { monthlyIncome: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                  <div className={`col-span-3 ${labelCellClass}`}>Monthly Ownership Cost</div>
                  <div className={`col-span-3 ${inputCellClass}`}>
                    <input 
                      type="number" 
                      value={asset.monthlyCost || ''} 
                      onChange={(e) => updateAsset(asset.id, { monthlyCost: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                </div>

                {/* Future Plans */}
                <div className="grid grid-cols-12 border-b border-gray-200">
                   <div className={`col-span-2 ${labelCellClass}`}>Plan to Sell?</div>
                   <div className={`col-span-2 ${inputCellClass} border-r border-gray-200 flex items-center`}>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={!!asset.planToSell} onChange={(e) => updateAsset(asset.id, { planToSell: e.target.checked })} className="accent-black w-3 h-3" />
                        <span className="text-xs uppercase font-bold text-[#686a6c]">Yes</span>
                      </label>
                   </div>
                   {asset.planToSell ? (
                     <>
                        <div className={`col-span-2 ${labelCellClass}`}>Target Year</div>
                        <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                          <input type="number" value={asset.targetSellYear || ''} onChange={(e) => updateAsset(asset.id, { targetSellYear: e.target.value })} className={inputClass} placeholder="YYYY" />
                        </div>
                        <div className={`col-span-2 ${labelCellClass}`}>Exp. Price</div>
                        <div className={`col-span-2 ${inputCellClass}`}>
                          <input type="number" value={asset.expectedSellPrice || ''} onChange={(e) => updateAsset(asset.id, { expectedSellPrice: e.target.value })} className={inputClass} placeholder="0.00" />
                        </div>
                     </>
                   ) : (
                     <div className="col-span-8 bg-gray-50"></div>
                   )}
                </div>

                <div className="grid grid-cols-12 border-b border-gray-200">
                   <div className={`col-span-3 ${labelCellClass}`}>Sell at Retirement?</div>
                   <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                      <select value={asset.sellAtRetirement || 'Unsure'} onChange={(e) => updateAsset(asset.id, { sellAtRetirement: e.target.value })} className={`${inputClass} cursor-pointer`}>
                        <option value="Yes">Yes</option>
                        <option value="No">No</option>
                        <option value="Unsure">Unsure</option>
                      </select>
                   </div>
                   <div className={`col-span-2 ${labelCellClass}`}>Estate Plan</div>
                   <div className={`col-span-4 ${inputCellClass}`}>
                      <select value={asset.estatePlan || ''} onChange={(e) => updateAsset(asset.id, { estatePlan: e.target.value })} className={`${inputClass} cursor-pointer`}>
                        <option value="">Select...</option>
                        <option value="Inherit by Spouse">Inherit by Spouse</option>
                        <option value="Children">Children</option>
                        <option value="Sell">Sell</option>
                        <option value="Charity">Charity</option>
                        <option value="Trust">Trust</option>
                        <option value="Other">Other</option>
                      </select>
                   </div>
                </div>

              </div>
             );
          })}
        </div>
      </div>

      {/* FINANCIAL ASSETS SECTION */}
      <div className="border-b border-gray-200">
        <div className="px-3 py-2 bg-gray-50 border-b border-gray-200">
           <span className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Investments & Financial Assets</span>
        </div>
        
        <div>
          {/* Active Financial Assets */}
          <div>
            {assets.filter(a => a.category === 'Financial').map((asset, index) => (
              <div key={asset.id} className="border-b-4 border-gray-100 last:border-b-0">
                <div className={sectionHeaderClass}>
                  <span className="text-[10px] font-bold uppercase text-blue-900">{asset.type} #{index + 1}</span>
                  <button 
                    type="button" 
                    onClick={() => removeAsset(asset.id)}
                    className="text-red-600 text-[10px] font-bold uppercase hover:text-red-800"
                  >
                    Remove
                  </button>
                </div>
                
                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-3 ${labelCellClass}`}>Company / Product Name</div>
                  <div className={`col-span-9 ${inputCellClass}`}>
                    <input 
                      type="text" 
                      value={asset.companyName || ''} 
                      onChange={(e) => updateAsset(asset.id, { companyName: e.target.value })} 
                      className={inputClass} 
                      placeholder="e.g. Allan Gray Equity"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-12 border-b border-gray-200">
                  <div className={`col-span-2 ${labelCellClass}`}>Balance</div>
                  <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="number" 
                      value={asset.currentBalance || ''} 
                      onChange={(e) => updateAsset(asset.id, { currentBalance: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Annual Return %</div>
                  <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
                    <input 
                      type="number" 
                      value={asset.projectedReturn || ''} 
                      onChange={(e) => updateAsset(asset.id, { projectedReturn: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.0"
                    />
                  </div>
                  <div className={`col-span-2 ${labelCellClass}`}>Monthly Contrib.</div>
                  <div className={`col-span-2 ${inputCellClass}`}>
                    <input 
                      type="number" 
                      value={asset.monthlyContribution || ''} 
                      onChange={(e) => updateAsset(asset.id, { monthlyContribution: e.target.value })} 
                      className={inputClass} 
                      placeholder="0.00"
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Quick Add Buttons */}
          <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50/30">
            <div className={`col-span-3 ${labelCellClass}`}>Quick Add Investment</div>
            <div className={`col-span-9 ${inputCellClass} flex flex-wrap gap-2 px-3 py-2`}>
              {financialTypes.map(type => (
                <button
                  key={type}
                  type="button"
                  onClick={() => addAsset('Financial', type)}
                  className="px-2 py-1 border border-gray-300 hover:border-blue-800 hover:text-blue-800 text-[10px] font-bold uppercase tracking-wider bg-white transition-colors"
                >
                  + {type}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AssetsSection;
