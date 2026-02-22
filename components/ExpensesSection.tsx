import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ExpensesData } from '../types';

interface ExpensesSectionProps {
  data: ExpensesData;
  onChange: (data: ExpensesData) => void;
  grossIncome: number;
}

// Move constants outside component to avoid recreation
const EXPENSE_FIELDS: Array<keyof ExpensesData> = [
  'fuelCost', 'autoInsurance', 'vehicleRepayment', 'maintenanceCost', 'tollsAndTracker',
  'groceries', 'diningOut', 'medicalAidPremium', 'gapCoverPremium', 'otherMedicalCosts',
  'childCare', 'educationalExpenses', 'cleaningServices', 'landscaping',
  'mortgagePayment', 'rentPayment', 'ratesAndTaxes', 'householdInsurance', 'otherHouseholdCosts',
  'electricity', 'gas', 'water', 'internet', 'dstv', 'trashCollection',
  'clothing', 'personalCare', 'petCare', 'hobbies', 'cellphoneData', 'travelBudget',
  'giftsDonations', 'charitableDonations',
  'creditCardPayments', 'personalLoans', 'studentLoans', 'otherDebt', 'childSupport'
];

const IMMEDIATE_SYNC_FIELDS = ['transportMode', 'hasMaintenancePlan', 'livingSituation', 'onFamilyPlan', 'openToMedicalOptions', 'openToGapOptions'];

const ExpensesSection: React.FC<ExpensesSectionProps> = ({ data, onChange, grossIncome }) => {
  const [localData, setLocalData] = useState(data);
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  useEffect(() => {
    setLocalData(data);
  }, [data]);

  // Memoize handleChange to prevent recreation
  const handleChange = useCallback(<K extends keyof ExpensesData>(field: K, value: ExpensesData[K]) => {
    const newData = { ...localData, [field]: value };
    setLocalData(newData);
    if (IMMEDIATE_SYNC_FIELDS.includes(field)) {
        onChange(newData);
    }
  }, [localData, onChange]);

  const handleSubscriptionChange = useCallback((subField: keyof ExpensesData['subscriptions'], value: string) => {
    const newSubs = { ...localData.subscriptions, [subField]: value };
    const newData = { ...localData, subscriptions: newSubs };
    setLocalData(newData);
  }, [localData]);

  const handleBlur = useCallback(() => {
    onChange(localData);
  }, [localData, onChange]);

  // Memoize expensive calculation
  const totalExpenses = useMemo(() => {
    let total = 0;
    
    for (const field of EXPENSE_FIELDS) {
      const value = localData[field];
      if (typeof value === 'string') {
        total += parseFloat(value) || 0;
      } else if (typeof value === 'number') {
        total += value;
      }
    }

    total += (parseFloat(localData.childEducationAnnual) || 0) / 12;

    if (localData.subscriptions) {
      for (const val of Object.values(localData.subscriptions)) {
        total += parseFloat(val) || 0;
      }
    }

    return total;
  }, [localData]);

  const disposableIncome = grossIncome - totalExpenses;

  const formatCurrency = useCallback((amount: number) => {
    if (!isMounted) return 'R 0.00';
    return amount.toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  }, [isMounted]);

  // Styles
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const sectionHeaderClass = "px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center";
  const sectionTitleClass = "text-[10px] font-bold uppercase text-black tracking-wider";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Monthly Expenses & Budget</h3>
      </div>

      {/* Gross Income Display */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-blue-50/50">
        <div className={`col-span-6 ${labelCellClass} text-blue-900`}>Monthly Gross Income (From Above)</div>
        <div className={`col-span-6 ${inputCellClass} font-bold text-blue-900`}>
          {formatCurrency(grossIncome)}
        </div>
      </div>

      {/* Transport */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Transport</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Transport Mode</div>
        <div className={`col-span-9 ${inputCellClass}`}>
          <select
            value={localData.transportMode || ''}
            onChange={(e) => handleChange('transportMode', e.target.value)}
            className={`${inputClass} cursor-pointer`}
          >
            <option value="">Select Mode</option>
            <option value="Owned Car">Owned Car</option>
            <option value="Financed Car">Financed Car</option>
            <option value="Transport">Public Transport / Uber</option>
          </select>
        </div>
      </div>
      
      {localData.transportMode && (
        <>
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Fuel Cost</div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
              <input type="number" value={localData.fuelCost || ''} onChange={(e) => handleChange('fuelCost', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
            </div>
            <div className={`col-span-3 ${labelCellClass}`}>Auto Insurance</div>
            <div className={`col-span-3 ${inputCellClass}`}>
              <input type="number" value={localData.autoInsurance || ''} onChange={(e) => handleChange('autoInsurance', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
            </div>
          </div>
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Policy Name</div>
            <div className={`col-span-9 ${inputCellClass}`}>
              <input type="text" value={localData.autoInsurancePolicy || ''} onChange={(e) => handleChange('autoInsurancePolicy', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Insurer Name" />
            </div>
          </div>
          
          {localData.transportMode === 'Financed Car' && (
            <div className="grid grid-cols-12 border-b border-gray-200">
              <div className={`col-span-3 ${labelCellClass}`}>Vehicle Repayment</div>
              <div className={`col-span-9 ${inputCellClass}`}>
                <input type="number" value={localData.vehicleRepayment || ''} onChange={(e) => handleChange('vehicleRepayment', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
              </div>
            </div>
          )}

          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Maintenance Plan?</div>
            <div className={`col-span-3 ${inputCellClass} border-r border-gray-200 flex items-center px-3 py-2`}>
               <label className="flex items-center gap-2 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                 <input type="checkbox" checked={localData.hasMaintenancePlan || false} onChange={(e) => handleChange('hasMaintenancePlan', e.target.checked)} className="accent-black w-3 h-3" />
                 <span className="text-xs font-bold uppercase text-[#686a6c]">Yes</span>
               </label>
            </div>
            {!localData.hasMaintenancePlan && (
               <>
                 <div className={`col-span-3 ${labelCellClass}`}>Est. Maint Cost</div>
                 <div className={`col-span-3 ${inputCellClass}`}>
                   <input type="number" value={localData.maintenanceCost || ''} onChange={(e) => handleChange('maintenanceCost', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
                 </div>
               </>
            )}
          </div>
          
          <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-3 ${labelCellClass}`}>Tolls / Tracker</div>
            <div className={`col-span-9 ${inputCellClass}`}>
              <input type="number" value={localData.tollsAndTracker || ''} onChange={(e) => handleChange('tollsAndTracker', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
            </div>
          </div>
        </>
      )}

      {/* Living Situation */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Living Situation</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Type</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
          <select value={localData.livingSituation || ''} onChange={(e) => handleChange('livingSituation', e.target.value)} className={`${inputClass} cursor-pointer`}>
            <option value="">Select...</option>
            <option value="Rent">Rent</option>
            <option value="Own">Own</option>
            <option value="Family">Family</option>
          </select>
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Time at Address</div>
        <div className={`col-span-3 ${inputCellClass}`}>
           <input type="text" value={localData.timeAtAddress || ''} onChange={(e) => handleChange('timeAtAddress', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="e.g. 5 years" />
        </div>
      </div>

      {/* Housing Expenses */}
      {localData.livingSituation && localData.livingSituation !== 'Family' && (
        <>
            <div className="grid grid-cols-12 border-b border-gray-200">
                <div className={`col-span-3 ${labelCellClass}`}>{localData.livingSituation === 'Own' ? 'Mortgage' : 'Rent'}</div>
                <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                    <input type="number" value={localData.livingSituation === 'Own' ? localData.mortgagePayment : localData.rentPayment || ''} onChange={(e) => handleChange(localData.livingSituation === 'Own' ? 'mortgagePayment' : 'rentPayment', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
                </div>
                {localData.livingSituation === 'Own' && (
                    <>
                        <div className={`col-span-3 ${labelCellClass}`}>Rates & Taxes</div>
                        <div className={`col-span-3 ${inputCellClass}`}>
                            <input type="number" value={localData.ratesAndTaxes || ''} onChange={(e) => handleChange('ratesAndTaxes', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
                        </div>
                    </>
                )}
            </div>
            <div className="grid grid-cols-12 border-b border-gray-200">
                 <div className={`col-span-3 ${labelCellClass}`}>Home Insurance</div>
                 <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
                     <input type="number" value={localData.householdInsurance || ''} onChange={(e) => handleChange('householdInsurance', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Contents/Building" />
                 </div>
                 <div className={`col-span-3 ${labelCellClass}`}>Policy Name</div>
                 <div className={`col-span-3 ${inputCellClass}`}>
                     <input type="text" value={localData.householdInsurancePolicy || ''} onChange={(e) => handleChange('householdInsurancePolicy', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Insurer Name" />
                 </div>
            </div>
            <div className="grid grid-cols-12 border-b border-gray-200">
                 <div className={`col-span-3 ${labelCellClass}`}>Other Household</div>
                 <div className={`col-span-9 ${inputCellClass}`}>
                     <input type="number" value={localData.otherHouseholdCosts || ''} onChange={(e) => handleChange('otherHouseholdCosts', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
                 </div>
            </div>
        </>
      )}
      
      {/* Utilities Grid */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Electricity</div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.electricity || ''} onChange={(e) => handleChange('electricity', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Water</div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.water || ''} onChange={(e) => handleChange('water', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Gas</div>
         <div className={`col-span-2 ${inputCellClass}`}>
             <input type="number" value={localData.gas || ''} onChange={(e) => handleChange('gas', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Internet</div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.internet || ''} onChange={(e) => handleChange('internet', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>DSTV</div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.dstv || ''} onChange={(e) => handleChange('dstv', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Trash/Refuse</div>
         <div className={`col-span-2 ${inputCellClass}`}>
             <input type="number" value={localData.trashCollection || ''} onChange={(e) => handleChange('trashCollection', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>
       <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Cleaning Services</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.cleaningServices || ''} onChange={(e) => handleChange('cleaningServices', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Landscaping</div>
         <div className={`col-span-3 ${inputCellClass}`}>
             <input type="number" value={localData.landscaping || ''} onChange={(e) => handleChange('landscaping', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>


      {/* Groceries */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Food & Lifestyle</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Groceries</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={localData.groceries || ''} onChange={(e) => handleChange('groceries', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Dining Out</div>
        <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={localData.diningOut || ''} onChange={(e) => handleChange('diningOut', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Clothing</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={localData.clothing || ''} onChange={(e) => handleChange('clothing', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Personal Care</div>
        <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={localData.personalCare || ''} onChange={(e) => handleChange('personalCare', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Gym, Salon, etc." />
        </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Pet Care</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={localData.petCare || ''} onChange={(e) => handleChange('petCare', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Hobbies</div>
        <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={localData.hobbies || ''} onChange={(e) => handleChange('hobbies', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
      </div>

      {/* Subscriptions - Compact */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Gym</div>
         <div className={`col-span-1 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.subscriptions?.gym || ''} onChange={(e) => handleSubscriptionChange('gym', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Netflix</div>
         <div className={`col-span-1 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.subscriptions?.netflix || ''} onChange={(e) => handleSubscriptionChange('netflix', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Youtube</div>
         <div className={`col-span-1 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.subscriptions?.youtube || ''} onChange={(e) => handleSubscriptionChange('youtube', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0" />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Spotify</div>
         <div className={`col-span-1 ${inputCellClass}`}>
             <input type="number" value={localData.subscriptions?.spotify || ''} onChange={(e) => handleSubscriptionChange('spotify', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Other Subs</div>
         <div className={`col-span-10 ${inputCellClass}`}>
             <input type="number" value={localData.subscriptions?.other || ''} onChange={(e) => handleSubscriptionChange('other', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Cellphone & Data</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
            <input type="number" value={localData.cellphoneData || ''} onChange={(e) => handleChange('cellphoneData', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
        <div className={`col-span-3 ${labelCellClass}`}>Travel Budget (Saved)</div>
        <div className={`col-span-3 ${inputCellClass}`}>
            <input type="number" value={localData.travelBudget || ''} onChange={(e) => handleChange('travelBudget', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
        </div>
      </div>

      {/* Medical */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Medical</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Medical Aid</div>
        <div className={`col-span-9 ${inputCellClass} flex gap-4`}>
           <label className="flex items-center gap-2 cursor-pointer">
             <input type="checkbox" checked={localData.onFamilyPlan || false} onChange={(e) => handleChange('onFamilyPlan', e.target.checked)} className="accent-black w-3 h-3" />
             <span className="text-xs font-bold uppercase text-[#686a6c]">Family Plan?</span>
           </label>
           <input type="text" value={localData.medicalAidPlan || ''} onChange={(e) => handleChange('medicalAidPlan', e.target.value)} onBlur={handleBlur} className={`${inputClass} border-b border-gray-200 w-32`} placeholder="Plan Name" />
           <input type="number" value={localData.medicalAidPremium || ''} onChange={(e) => handleChange('medicalAidPremium', e.target.value)} onBlur={handleBlur} className={`${inputClass} border-b border-gray-200 w-24`} placeholder="Premium" />
           <label className="flex items-center gap-2 cursor-pointer ml-auto px-2 py-1 hover:bg-gray-100 rounded">
             <input type="checkbox" checked={localData.openToMedicalOptions || false} onChange={(e) => handleChange('openToMedicalOptions', e.target.checked)} className="accent-black w-3 h-3" />
             <span className="text-xs font-bold uppercase text-blue-800">Open to Options</span>
           </label>
        </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Gap Cover</div>
        <div className={`col-span-9 ${inputCellClass} flex gap-4 px-3 py-2`}>
           <input type="text" value={localData.gapCoverPlan || ''} onChange={(e) => handleChange('gapCoverPlan', e.target.value)} onBlur={handleBlur} className={`${inputClass} border-b border-gray-200 w-32`} placeholder="Plan Name" />
           <input type="number" value={localData.gapCoverPremium || ''} onChange={(e) => handleChange('gapCoverPremium', e.target.value)} onBlur={handleBlur} className={`${inputClass} border-b border-gray-200 w-24`} placeholder="Premium" />
           <label className="flex items-center gap-2 cursor-pointer ml-auto px-2 py-1 hover:bg-gray-100 rounded">
             <input type="checkbox" checked={localData.openToGapOptions || false} onChange={(e) => handleChange('openToGapOptions', e.target.checked)} className="accent-black w-3 h-3" />
             <span className="text-xs font-bold uppercase text-blue-800">Open to Options</span>
           </label>
        </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Other Medical</div>
         <div className={`col-span-9 ${inputCellClass}`}>
             <input type="number" value={localData.otherMedicalCosts || ''} onChange={(e) => handleChange('otherMedicalCosts', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Meds / Consults" />
         </div>
      </div>

      {/* Children & Education */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Children & Education</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Child Care</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.childCare || ''} onChange={(e) => handleChange('childCare', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Child Support</div>
         <div className={`col-span-3 ${inputCellClass}`}>
             <input type="number" value={localData.childSupport || ''} onChange={(e) => handleChange('childSupport', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Alimony etc." />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Education (Annual)</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.childEducationAnnual || ''} onChange={(e) => handleChange('childEducationAnnual', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Other Edu Costs</div>
         <div className={`col-span-3 ${inputCellClass}`}>
             <input type="number" value={localData.educationalExpenses || ''} onChange={(e) => handleChange('educationalExpenses', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="Monthly" />
         </div>
      </div>

      {/* Giving & Debt */}
      <div className={sectionHeaderClass}>
        <span className={sectionTitleClass}>Giving & Debt</span>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Gifts</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.giftsDonations || ''} onChange={(e) => handleChange('giftsDonations', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Donations/Tithe</div>
         <div className={`col-span-3 ${inputCellClass}`}>
             <input type="number" value={localData.charitableDonations || ''} onChange={(e) => handleChange('charitableDonations', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Credit Cards?</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200 flex items-center`}>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={localData.hasCreditCard || false} onChange={(e) => handleChange('hasCreditCard', e.target.checked)} className="accent-black w-3 h-3" />
              <span className="text-xs font-bold uppercase text-[#686a6c]">Yes</span>
            </label>
         </div>
         {localData.hasCreditCard && (
           <>
             <div className={`col-span-3 ${labelCellClass}`}>Monthly Payment</div>
             <div className={`col-span-3 ${inputCellClass}`}>
                 <input type="number" value={localData.creditCardPayments || ''} onChange={(e) => handleChange('creditCardPayments', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
             </div>
           </>
         )}
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Personal Loans</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.personalLoans || ''} onChange={(e) => handleChange('personalLoans', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Student Loans</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
             <input type="number" value={localData.studentLoans || ''} onChange={(e) => handleChange('studentLoans', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Other Debt</div>
         <div className={`col-span-3 ${inputCellClass}`}>
             <input type="number" value={localData.otherDebt || ''} onChange={(e) => handleChange('otherDebt', e.target.value)} onBlur={handleBlur} className={inputClass} placeholder="0.00" />
         </div>
      </div>

      {/* Totals */}
      <div className="border-t-2 border-black mt-4">
        <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-100">
            <div className={`col-span-6 ${labelCellClass} text-black font-extrabold`}>Total Monthly Expenses</div>
            <div className={`col-span-6 ${inputCellClass} font-bold text-red-700`}>
                {formatCurrency(totalExpenses)}
            </div>
        </div>
        <div className="grid grid-cols-12 border-b border-gray-200">
            <div className={`col-span-6 ${labelCellClass} text-black font-extrabold`}>Disposable Income</div>
            <div className={`col-span-6 ${inputCellClass} font-bold ${disposableIncome >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                {formatCurrency(disposableIncome)}
            </div>
        </div>
      </div>

    </div>
  );
};

export default ExpensesSection;
