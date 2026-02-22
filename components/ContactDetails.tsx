'use client';

import React from 'react';
import { ContactDetailsData } from '../types';
import NoteButton from './NoteButton';

interface ContactDetailsProps {
  data: ContactDetailsData;
  notes: Record<string, string>;
  onChange: (data: ContactDetailsData) => void;
  onNoteChange: (sectionId: string, fieldId: string, value: string) => void;
}

const ContactDetails: React.FC<ContactDetailsProps> = ({ data, notes, onChange, onNoteChange }) => {
  const SECTION_ID = 'contact';
  const hasMailingAddress = Boolean(
    data.mailingAddress?.street ||
      data.mailingAddress?.city ||
      data.mailingAddress?.province ||
      data.mailingAddress?.zipCode
  );
  const showMailingAddress = Boolean(data.mailingAddressDifferent || hasMailingAddress);

  const handleAddressChange = (type: 'primary' | 'mailing', field: string, value: string) => {
    if (type === 'primary') {
      onChange({
        ...data,
        primaryAddress: { ...data.primaryAddress, [field]: value }
      });
    } else {
      onChange({
        ...data,
        mailingAddress: { ...data.mailingAddress, [field]: value }
      });
    }
  };

  const handleCheckboxChange = (field: keyof ContactDetailsData, value: string) => {
    const currentValues = (data[field] as string[]) || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter(v => v !== value)
      : [...currentValues, value];
    onChange({ ...data, [field]: newValues });
  };

  // Shared classes
  const labelCellClass = "px-3 py-2 border-r border-gray-200 flex items-center text-[10px] font-bold uppercase text-[#686a6c] tracking-wider";
  const inputCellClass = "relative";
  const inputClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black placeholder-gray-300 font-serif px-3 py-2";
  const selectClass = "w-full bg-transparent border-none focus:ring-0 focus:outline-none text-sm text-black font-serif appearance-none cursor-pointer px-3 py-2";

  return (
    <div className="border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Contact Information</h3>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
        {/* Primary Address */}
        <div className={`col-span-2 ${labelCellClass}`}>Primary Address</div>
        <div className={`col-span-10 grid grid-cols-12`}>
          <div className="col-span-12 grid grid-cols-12 border-b border-gray-200 last:border-b-0">
            <div className={`col-span-12 ${inputCellClass} border-b border-gray-200`}>
              <input 
                type="text"
                value={data.primaryAddress?.street || ''}
                onChange={(e) => handleAddressChange('primary', 'street', e.target.value)}
                className={inputClass}
                placeholder="Street Address"
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
                <NoteButton sectionId={SECTION_ID} fieldId="primaryAddress-street" notes={notes} onNoteChange={onNoteChange} />
              </div>
            </div>
            <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
              <input 
                type="text"
                value={data.primaryAddress?.city || ''}
                onChange={(e) => handleAddressChange('primary', 'city', e.target.value)}
                className={inputClass}
                placeholder="City"
              />
            </div>
            <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
              <input 
                type="text"
                value={data.primaryAddress?.province || ''}
                onChange={(e) => handleAddressChange('primary', 'province', e.target.value)}
                className={inputClass}
                placeholder="Province"
              />
            </div>
            <div className={`col-span-3 ${inputCellClass}`}>
              <input 
                type="text"
                value={data.primaryAddress?.zipCode || ''}
                onChange={(e) => handleAddressChange('primary', 'zipCode', e.target.value)}
                className={inputClass}
                placeholder="Zip Code"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Mailing Address Toggle & Fields */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-2 ${labelCellClass}`}>
           <label className="flex items-center gap-2 cursor-pointer">
             <input 
               type="checkbox"
               checked={showMailingAddress}
               onChange={(e) => {
                 const checked = e.target.checked;
                 onChange({
                   ...data,
                   mailingAddressDifferent: checked,
                   mailingAddress: checked
                     ? data.mailingAddress
                     : { street: '', city: '', province: '', zipCode: '' }
                 });
               }}
               className="accent-black w-3 h-3"
             />
             <span className="text-[10px] font-bold uppercase text-[#686a6c]">Mailing Address Different?</span>
           </label>
        </div>
        <div className="col-span-10">
          {showMailingAddress && (
            <div className="grid grid-cols-12">
               <div className={`col-span-12 ${inputCellClass} border-b border-gray-200`}>
                 <input 
                   type="text"
                   value={data.mailingAddress?.street || ''}
                   onChange={(e) => handleAddressChange('mailing', 'street', e.target.value)}
                   className={inputClass}
                   placeholder="Mailing Street Address"
                 />
               </div>
               <div className={`col-span-5 ${inputCellClass} border-r border-gray-200`}>
                 <input 
                   type="text"
                   value={data.mailingAddress?.city || ''}
                   onChange={(e) => handleAddressChange('mailing', 'city', e.target.value)}
                   className={inputClass}
                   placeholder="City"
                 />
               </div>
               <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
                 <input 
                   type="text"
                   value={data.mailingAddress?.province || ''}
                   onChange={(e) => handleAddressChange('mailing', 'province', e.target.value)}
                   className={inputClass}
                   placeholder="Province"
                 />
               </div>
               <div className={`col-span-3 ${inputCellClass}`}>
                 <input 
                   type="text"
                   value={data.mailingAddress?.zipCode || ''}
                   onChange={(e) => handleAddressChange('mailing', 'zipCode', e.target.value)}
                   className={inputClass}
                   placeholder="Zip Code"
                 />
               </div>
            </div>
          )}
        </div>
      </div>

      {/* Phone Numbers */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-2 ${labelCellClass}`}>Primary Phone</div>
        <div className={`col-span-4 ${inputCellClass} border-r border-gray-200 flex items-center gap-2 pr-3`}>
          <input 
            type="tel"
            value={data.primaryPhone || ''}
            onChange={(e) => onChange({...data, primaryPhone: e.target.value})}
            className={`${inputClass} flex-1 min-w-0`}
            placeholder="(000) 000-0000"
          />
          <div className="flex gap-1">
             {['Mobile', 'Home', 'Work'].map(type => (
               <button
                 key={type}
                 type="button"
                 onClick={() => onChange({...data, primaryPhoneType: type})}
                 className={`text-[8px] uppercase px-1 py-0.5 border transition-colors ${data.primaryPhoneType === type ? 'border-black text-black font-bold' : 'text-gray-400 border-gray-200 hover:border-gray-400'}`}
               >
                 {type === 'Mobile' ? 'Mob' : type}
               </button>
             ))}
          </div>
        </div>

        <div className={`col-span-2 ${labelCellClass}`}>Secondary Phone</div>
        <div className={`col-span-4 ${inputCellClass} flex items-center gap-2 pr-3`}>
          <input 
            type="tel"
            value={data.secondaryPhone || ''}
            onChange={(e) => onChange({...data, secondaryPhone: e.target.value})}
            className={`${inputClass} flex-1 min-w-0`}
            placeholder="(000) 000-0000"
          />
           <div className="flex gap-1">
             {['Mobile', 'Home', 'Work'].map(type => (
               <button
                 key={type}
                 type="button"
                 onClick={() => onChange({...data, secondaryPhoneType: type})}
                 className={`text-[8px] uppercase px-1 py-0.5 border transition-colors ${data.secondaryPhoneType === type ? 'border-black text-black font-bold' : 'text-gray-400 border-gray-200 hover:border-gray-400'}`}
               >
                 {type === 'Mobile' ? 'Mob' : type}
               </button>
             ))}
          </div>
        </div>
      </div>

      {/* WhatsApp & Email */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-2 ${labelCellClass}`}>WhatsApp</div>
        <div className={`col-span-4 ${inputCellClass} border-r border-gray-200 flex items-center gap-2 pr-3`}>
          <input 
            type="tel"
            value={data.whatsapp || ''}
            onChange={(e) => onChange({...data, whatsapp: e.target.value})}
            className={`${inputClass} flex-1`}
            placeholder={data.whatsappSameAsPhone ? "Same as Primary" : "(000) 000-0000"}
            disabled={data.whatsappSameAsPhone}
          />
          <label className="flex items-center gap-1 cursor-pointer whitespace-nowrap">
             <input 
               type="checkbox" 
               checked={!!data.whatsappSameAsPhone} 
               onChange={(e) => {
                 const isChecked = e.target.checked;
                 onChange({
                   ...data, 
                   whatsappSameAsPhone: isChecked,
                   whatsapp: isChecked ? data.primaryPhone : data.whatsapp
                 });
               }}
               className="accent-black w-3 h-3" 
             />
             <span className="text-[8px] font-bold uppercase text-[#686a6c]">Same as Phone</span>
          </label>
        </div>
        <div className={`col-span-2 ${labelCellClass}`}>Email</div>
        <div className={`col-span-4 ${inputCellClass}`}>
          <input 
            type="email"
            value={data.email || ''}
            onChange={(e) => onChange({...data, email: e.target.value})}
            className={inputClass}
            placeholder="email@example.com"
          />
        </div>
      </div>

      {/* Insurance Portfolio Review */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Insurance Portfolio Review</h3>
      </div>
      
      {/* Headers */}
      <div className="grid grid-cols-12 border-b border-gray-200 bg-gray-50">
        <div className="col-span-3 px-3 py-2 border-r border-gray-200 text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Type</div>
        <div className="col-span-3 px-3 py-2 border-r border-gray-200 text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Current Coverage</div>
        <div className="col-span-2 px-3 py-2 border-r border-gray-200 text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Recommendation</div>
        <div className="col-span-2 px-3 py-2 border-r border-gray-200 text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Target Cover</div>
        <div className="col-span-2 px-3 py-2 text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Target Premium</div>
      </div>

      {/* Life Insurance */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Life Insurance</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.lifeInsuranceTotal || ''} 
             onChange={(e) => onChange({...data, lifeInsuranceTotal: e.target.value})}
             className={inputClass}
             placeholder="Amount"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <select
             value={data.lifeInsuranceRecommend || ''}
             onChange={(e) => onChange({...data, lifeInsuranceRecommend: e.target.value as ContactDetailsData['lifeInsuranceRecommend']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Leave">Leave</option>
             <option value="Modify">Modify</option>
             <option value="Replace">Replace</option>
           </select>
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.lifeInsuranceTargetCover || ''} 
             onChange={(e) => onChange({...data, lifeInsuranceTargetCover: e.target.value})}
             className={inputClass}
             placeholder="Target"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.lifeInsuranceTargetPremium || ''} 
             onChange={(e) => onChange({...data, lifeInsuranceTargetPremium: e.target.value})}
             className={inputClass}
             placeholder="Premium"
           />
         </div>
      </div>

      {/* Critical Illness */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Critical Illness</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.criticalIllnessCoverage || ''} 
             onChange={(e) => onChange({...data, criticalIllnessCoverage: e.target.value})}
             className={inputClass}
             placeholder="Amount"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <select
             value={data.criticalIllnessRecommend || ''}
             onChange={(e) => onChange({...data, criticalIllnessRecommend: e.target.value as ContactDetailsData['criticalIllnessRecommend']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Leave">Leave</option>
             <option value="Modify">Modify</option>
             <option value="Replace">Replace</option>
           </select>
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.criticalIllnessTargetCover || ''} 
             onChange={(e) => onChange({...data, criticalIllnessTargetCover: e.target.value})}
             className={inputClass}
             placeholder="Target"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.criticalIllnessTargetPremium || ''} 
             onChange={(e) => onChange({...data, criticalIllnessTargetPremium: e.target.value})}
             className={inputClass}
             placeholder="Premium"
           />
         </div>
      </div>

      {/* Disability Coverage */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Disability Coverage</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200 flex gap-2`}>
           <input 
             type="text" 
             value={data.disabilityCoverage || ''} 
             onChange={(e) => onChange({...data, disabilityCoverage: e.target.value})}
             className={`${inputClass} flex-1`}
             placeholder="% or Amount"
           />
           <select
             value={data.disabilityCoverageType || ''}
             onChange={(e) => onChange({...data, disabilityCoverageType: e.target.value as ContactDetailsData['disabilityCoverageType']})}
             className="text-[10px] bg-transparent border-none focus:ring-0 text-gray-500 font-bold uppercase cursor-pointer"
           >
             <option value="">Type</option>
             <option value="Short-Term">Short-Term</option>
             <option value="Long-Term">Long-Term</option>
           </select>
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <select
             value={data.disabilityCoverageRecommend || ''}
             onChange={(e) => onChange({...data, disabilityCoverageRecommend: e.target.value as ContactDetailsData['disabilityCoverageRecommend']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Leave">Leave</option>
             <option value="Modify">Modify</option>
             <option value="Replace">Replace</option>
           </select>
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.disabilityCoverageTargetCover || ''} 
             onChange={(e) => onChange({...data, disabilityCoverageTargetCover: e.target.value})}
             className={inputClass}
             placeholder="Target"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.disabilityCoverageTargetPremium || ''} 
             onChange={(e) => onChange({...data, disabilityCoverageTargetPremium: e.target.value})}
             className={inputClass}
             placeholder="Premium"
           />
         </div>
      </div>

      {/* Income Protection */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Income Protection</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.incomeProtection || ''} 
             onChange={(e) => onChange({...data, incomeProtection: e.target.value})}
             className={inputClass}
             placeholder="Amount/month"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <select
             value={data.incomeProtectionRecommend || ''}
             onChange={(e) => onChange({...data, incomeProtectionRecommend: e.target.value as ContactDetailsData['incomeProtectionRecommend']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Leave">Leave</option>
             <option value="Modify">Modify</option>
             <option value="Replace">Replace</option>
           </select>
         </div>
         <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.incomeProtectionTargetCover || ''} 
             onChange={(e) => onChange({...data, incomeProtectionTargetCover: e.target.value})}
             className={inputClass}
             placeholder="Target"
           />
         </div>
         <div className={`col-span-2 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.incomeProtectionTargetPremium || ''} 
             onChange={(e) => onChange({...data, incomeProtectionTargetPremium: e.target.value})}
             className={inputClass}
             placeholder="Premium"
           />
         </div>
      </div>

      {/* Health & Lifestyle */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Health & Lifestyle</h3>
      </div>
      
      {/* Hazardous Activities */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Hazardous Activities</div>
        <div className={`col-span-9 ${inputCellClass} flex flex-wrap gap-4 items-center`}>
          {['Skydiving', 'Racing'].map(activity => (
            <label key={activity} className="flex items-center gap-1 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
              <input 
                type="checkbox"
                checked={(data.hazardousActivities || []).includes(activity)}
                onChange={() => handleCheckboxChange('hazardousActivities', activity)}
                className="accent-black w-3 h-3"
              />
              <span className="text-xs">{activity}</span>
            </label>
          ))}
          <div className="flex items-center gap-1 flex-1 min-w-[150px]">
            <span className="text-xs whitespace-nowrap">Other:</span>
            <input 
              type="text"
              value={data.hazardousActivitiesOther || ''}
              onChange={(e) => onChange({...data, hazardousActivitiesOther: e.target.value})}
              className={`${inputClass} border-b border-gray-200 !px-0 !py-0 focus:border-black`}
              placeholder="Specify..."
            />
          </div>
        </div>
      </div>

      {/* Criminal History & Substance Use */}
      <div className="grid grid-cols-12 border-b border-gray-200">
        <div className={`col-span-3 ${labelCellClass}`}>Criminal History</div>
        <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <select
             value={data.criminalHistory || ''}
             onChange={(e) => onChange({...data, criminalHistory: e.target.value as ContactDetailsData['criminalHistory']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
           </select>
        </div>
        <div className={`col-span-6 ${inputCellClass}`}>
           <div className="text-[10px] font-bold uppercase text-[#686a6c] mb-2">Substance Use:</div>
           <div className="grid grid-cols-3 gap-2">
             {['Tobacco', 'Cigarettes', 'Marijuana', 'Opioids', 'Alcohol', 'None'].map(item => (
               <label key={item} className="flex items-center gap-1 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
                  <input
                    type="checkbox"
                    checked={(data.substanceUse || []).includes(item)}
                    onChange={() => handleCheckboxChange('substanceUse', item)}
                    className="accent-black w-3 h-3"
                  />
                  <span className="text-[9px] leading-tight">{item}</span>
               </label>
             ))}
           </div>
        </div>
      </div>

      {/* Frequency & History */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>If Yes - Frequency</div>
         <div className={`col-span-9 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.substanceFrequency || ''} 
             onChange={(e) => onChange({...data, substanceFrequency: e.target.value})}
             className={inputClass}
           />
         </div>
      </div>
      
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-6 ${labelCellClass}`}>Have you ever applied for any type of insurance before?</div>
         <div className={`col-span-6 ${inputCellClass} flex gap-4`}>
            <label className="flex items-center gap-1 cursor-pointer px-2 py-1 hover:bg-gray-100 rounded">
              <input 
                type="radio"
                name="appliedForInsurance"
                checked={data.appliedForInsurance === true}
                onChange={() => onChange({...data, appliedForInsurance: true})}
                className="accent-black w-3 h-3"
              />
              <span className="text-xs">Yes</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input 
                type="radio"
                name="appliedForInsurance"
                checked={data.appliedForInsurance === false}
                onChange={() => onChange({...data, appliedForInsurance: false})}
                className="accent-black w-3 h-3"
              />
              <span className="text-xs">No</span>
            </label>
         </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-4 ${labelCellClass}`}>Was it Declined/Rated for any reason?</div>
         <div className={`col-span-8 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.declinedOrRated || ''} 
             onChange={(e) => onChange({...data, declinedOrRated: e.target.value})}
             className={inputClass}
             placeholder="Details..."
           />
         </div>
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Height (cm)</div>
         <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
           <input 
             type="text" 
             value={data.height || ''} 
             onChange={(e) => onChange({...data, height: e.target.value})}
             className={inputClass}
           />
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Weight (Kg)</div>
         <div className={`col-span-4 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.weight || ''} 
             onChange={(e) => onChange({...data, weight: e.target.value})}
             className={inputClass}
           />
         </div>
      </div>

      {/* Estate Planning */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Estate Planning</h3>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Will Status</div>
         <div className={`col-span-4 ${inputCellClass} border-r border-gray-200`}>
           <select 
             value={data.willStatus || ''}
             onChange={(e) => onChange({...data, willStatus: e.target.value as ContactDetailsData['willStatus']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
             <option value="In Progress">In Progress</option>
           </select>
         </div>
         <div className={`col-span-2 ${labelCellClass}`}>Last Updated</div>
         <div className={`col-span-4 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.willLastUpdated || ''} 
             onChange={(e) => onChange({...data, willLastUpdated: e.target.value})}
             className={inputClass}
             placeholder="MM/YYYY"
           />
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Trust</div>
         <div className={`col-span-10 ${inputCellClass}`}>
           <select
             value={data.trustStatus || ''}
             onChange={(e) => onChange({...data, trustStatus: e.target.value as ContactDetailsData['trustStatus']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
             <option value="Already in place">Already in place</option>
           </select>
         </div>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Inheritance Expectations</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <select 
             value={data.inheritanceExpectations || ''}
             onChange={(e) => onChange({...data, inheritanceExpectations: e.target.value as ContactDetailsData['inheritanceExpectations']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
             <option value="Uncertain">Uncertain</option>
           </select>
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Estimated Amount</div>
         <div className={`col-span-3 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.inheritanceAmount || ''} 
             onChange={(e) => onChange({...data, inheritanceAmount: e.target.value})}
             className={inputClass}
             placeholder="Amount"
           />
         </div>
      </div>

      {/* Financial Priorities & Concerns */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Financial Priorities & Concerns</h3>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Largest Financial Concern</div>
         <div className={`col-span-9 ${inputCellClass}`}>
           <input 
             type="text" 
             value={data.largestFinancialConcern || ''} 
             onChange={(e) => onChange({...data, largestFinancialConcern: e.target.value})}
             className={inputClass}
           />
         </div>
      </div>
      
      <div className="px-3 py-2 border-b border-gray-200">
        <h4 className="text-[10px] font-bold uppercase text-[#686a6c] tracking-wider">Priority Rankings (1–10)</h4>
      </div>
      
      {/* Priority Fields - Compact Grid */}
      <div className="grid grid-cols-12 border-b border-gray-200">
         {[
           { label: 'Emergency Fund', field: 'priorityEmergencyFund' },
           { label: 'Debt Payoff', field: 'priorityDebtPayoff' },
           { label: 'Retirement', field: 'priorityRetirement' },
           { label: 'Insurance Coverage', field: 'priorityInsuranceCoverage' }
         ].map((item, i) => (
           <React.Fragment key={item.field}>
             <div className={`col-span-2 ${labelCellClass} ${i % 2 !== 0 ? 'border-l' : ''}`}>{item.label}</div>
             <div className={`col-span-1 ${inputCellClass} border-r border-gray-200`}>
               <input 
                 type="number" min="1" max="10"
                 value={data[item.field as keyof ContactDetailsData] as string || ''} 
                 onChange={(e) => onChange({...data, [item.field]: e.target.value})}
                 className={`${inputClass} text-center`}
               />
             </div>
           </React.Fragment>
         ))}
      </div>
      
      <div className="grid grid-cols-12 border-b border-gray-200">
         {[
           { label: 'Estate Preservation', field: 'priorityEstatePreservation' },
           { label: 'Cash Flow Mgmt', field: 'priorityCashFlow' },
           { label: 'Education Funding', field: 'priorityEducationFunding' }
         ].map((item, i) => (
           <React.Fragment key={item.field}>
             <div className={`col-span-2 ${labelCellClass} ${i !== 0 ? 'border-l' : ''}`}>{item.label}</div>
             <div className={`col-span-2 ${inputCellClass} border-r border-gray-200`}>
               <input 
                 type="number" min="1" max="10"
                 value={data[item.field as keyof ContactDetailsData] as string || ''} 
                 onChange={(e) => onChange({...data, [item.field]: e.target.value})}
                 className={`${inputClass} text-center`}
               />
             </div>
           </React.Fragment>
         ))}
      </div>

      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-2 ${labelCellClass}`}>Income Replacement</div>
         <div className={`col-span-1 ${inputCellClass} border-r border-gray-200`}>
            <input 
              type="number" min="1" max="10"
              value={data.priorityIncomeReplacement || ''} 
              onChange={(e) => onChange({...data, priorityIncomeReplacement: e.target.value})}
              className={`${inputClass} text-center`}
            />
         </div>
         <div className={`col-span-9 ${inputCellClass} flex items-center gap-2 pr-3`}>
            <input 
              type="text" 
              value={data.incomeReplacementAmount || ''} 
              onChange={(e) => onChange({...data, incomeReplacementAmount: e.target.value})}
              className={`${inputClass} w-32 border-b border-gray-200 text-center`}
              placeholder="Amount/month"
            />
            <span className="text-xs">for</span>
            <input 
              type="text" 
              value={data.incomeReplacementYears || ''} 
              onChange={(e) => onChange({...data, incomeReplacementYears: e.target.value})}
              className={`${inputClass} w-20 border-b border-gray-200 text-center`}
              placeholder="Years"
            />
            <span className="text-xs">Years</span>
         </div>
      </div>

      {/* Risk & Implementation */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-bold uppercase text-black tracking-wider">Risk & Implementation</h3>
      </div>
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Monthly Budget Established</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <select 
             value={data.budgetEstablished || ''}
             onChange={(e) => onChange({...data, budgetEstablished: e.target.value as ContactDetailsData['budgetEstablished']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
           </select>
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Investment Risk Tolerance</div>
         <div className={`col-span-3 ${inputCellClass}`}>
           <select 
             value={data.riskTolerance || ''}
             onChange={(e) => onChange({...data, riskTolerance: e.target.value as ContactDetailsData['riskTolerance']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Low">Low</option>
             <option value="Low-Medium">Low-Medium</option>
             <option value="Medium">Medium</option>
             <option value="Medium-High">Medium-High</option>
             <option value="High">High</option>
           </select>
         </div>
      </div>
      
      <div className="grid grid-cols-12 border-b border-gray-200">
         <div className={`col-span-3 ${labelCellClass}`}>Potential Barriers</div>
         <div className={`col-span-3 ${inputCellClass} border-r border-gray-200`}>
           <select 
             value={data.implementationBarriers || ''}
             onChange={(e) => onChange({...data, implementationBarriers: e.target.value as ContactDetailsData['implementationBarriers']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="None">None</option>
             <option value="Budget Constraints">Budget Constraints</option>
             <option value="Uncertainty">Uncertainty</option>
             <option value="Timing">Timing</option>
           </select>
         </div>
         <div className={`col-span-3 ${labelCellClass}`}>Openness to Immediate Action</div>
         <div className={`col-span-3 ${inputCellClass}`}>
           <select 
             value={data.openToImmediateAction || ''}
             onChange={(e) => onChange({...data, openToImmediateAction: e.target.value as ContactDetailsData['openToImmediateAction']})}
             className={selectClass}
           >
             <option value="">Select...</option>
             <option value="Yes">Yes</option>
             <option value="No">No</option>
           </select>
         </div>
      </div>
      
      <div className="grid grid-cols-12 border-b border-gray-200 last:border-b-0">
         <div className={`col-span-2 ${labelCellClass}`}>Notes</div>
         <div className={`col-span-10 ${inputCellClass}`}>
           <textarea 
             value={data.notes || ''} 
             onChange={(e) => onChange({...data, notes: e.target.value})}
             className={`${inputClass} resize-none`}
             rows={3}
             placeholder="Additional notes..."
           />
         </div>
      </div>
    </div>
  );
};

export default ContactDetails;
