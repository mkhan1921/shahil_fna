
import React from 'react';
import { FormData, FamilyMember } from '../types';

interface PDFTemplateProps {
  data: FormData;
}

const PDFTemplate: React.FC<PDFTemplateProps> = ({ data }) => {
  const primary = data.primaryMember || {};
  const relation = data.relationshipStatus || {};
  const expenses = data.expenses || {};

  // Styles
  const containerStyle = {
    padding: '40px',
    maxWidth: '800px',
    margin: '0 auto',
    fontFamily: "'Times New Roman', Times, serif",
    color: '#000000',
    fontSize: '12px',
    lineHeight: '1.5'
  };

  const headerStyle = {
    borderBottom: '2px solid #000000',
    paddingBottom: '16px',
    marginBottom: '32px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start'
  };

  const sectionHeaderStyle = {
    fontSize: '14px',
    fontWeight: 'bold',
    color: '#000000',
    textTransform: 'uppercase' as const,
    borderBottom: '1px solid #000000',
    paddingBottom: '4px',
    marginBottom: '12px',
    marginTop: '24px'
  };

  const labelStyle = {
    display: 'block',
    fontSize: '10px',
    color: '#686a6c', // Nardo Grey
    textTransform: 'uppercase' as const,
    fontWeight: 'bold',
    marginBottom: '2px'
  };

  const valueStyle = {
    fontSize: '12px',
    fontWeight: 'normal',
    color: '#000000'
  };

  const grid3Style = {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr 1fr',
    gap: '16px',
    marginBottom: '16px'
  };

  const grid2Style = {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '16px',
    marginBottom: '16px'
  };

  const formatCurrency = (val: string | number | undefined) => {
    if (val === undefined || val === null) return 'R 0.00';
    const num = typeof val === 'string' ? parseFloat(val) : val;
    return (num || 0).toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
  };
  
  // Calculate Asset Totals
  const totalAssetValue = (data.assets || []).reduce((sum, asset) => sum + (parseFloat(asset.currentValue || asset.currentBalance || '0') || 0), 0);
  const totalLiabilities = (data.liabilities || []).reduce((sum, item) => sum + (parseFloat(item.currentBalance || '0') || 0), 0);
  const netWorth = totalAssetValue - totalLiabilities;

  // --- Retirement Calculations ---
  const totalRetirementSavings = (data.retirement.retirementFunds || []).reduce((sum, f) => sum + (parseFloat(f.currentBalance) || 0), 0);
  
  // Tax on Lump Sum (Retirement Table)
  const oneThirdLumpSum = totalRetirementSavings / 3;
  let taxOnLumpSum = 0;
  if (oneThirdLumpSum > 1155000) {
      taxOnLumpSum = 143550 + ((oneThirdLumpSum - 1155000) * 0.36);
  } else if (oneThirdLumpSum > 770000) {
      taxOnLumpSum = 39600 + ((oneThirdLumpSum - 770000) * 0.27);
  } else if (oneThirdLumpSum > 550000) {
      taxOnLumpSum = (oneThirdLumpSum - 550000) * 0.18;
  }

  // Earliest Age Calculation
  const calculateEarliestRetirementAge = () => {
    const targetIncome = parseFloat(data.retirement.targetMonthlyIncome || '0');
    const currentAge = parseFloat(primary.currentAge as string) || 0;
    if (targetIncome <= 0 || currentAge <= 0) return null;

    const withdrawalRateMonthly = 0.008;
    const targetCorpus = targetIncome / withdrawalRateMonthly;
    
    const r = 0.10 / 12; // 10% Nominal
    
    // Contributions
    const monthlySalary = parseFloat(data.employment.primaryIncomeAmount || '0') || 0;
    const empMatch = parseFloat(data.retirement.employerMatchPercentage || '0') || 0;
    const empContrib = parseFloat(data.retirement.employerContribution || '0') || (monthlySalary * (empMatch / 100));
    const ownContrib = parseFloat(data.retirement.employeeContribution || '0') || 0;
    const totalMonthlyContrib = empContrib + ownContrib;

    const PMT = totalMonthlyContrib;
    const PV = totalRetirementSavings;

    const numerator = targetCorpus + (PMT / r);
    const denominator = PV + (PMT / r);
    
    if (denominator <= 0) return null;
    const ratio = numerator / denominator;
    if (ratio <= 0) return null;
    
    const nMonths = Math.log(ratio) / Math.log(1 + r);
    if (nMonths < 0) return currentAge;
    
    return currentAge + (nMonths / 12);
  };
  
  const earliestAge = calculateEarliestRetirementAge();
  const currentPotentialMonthlyIncome = netWorth * 0.008;


  return (
    <div style={containerStyle}>
      
      {/* Header */}
      <div style={headerStyle}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 'bold', margin: '0 0 4px 0' }}>CLIENT INTAKE REPORT</h1>
          <p style={{ fontSize: '14px', margin: 0, textTransform: 'uppercase' }}>{data.clientName}</p>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: '10px', color: '#686a6c', marginBottom: '2px' }}>DATE: <span style={{ color: '#000000' }}>{new Date().toLocaleDateString()}</span></div>
          <div style={{ marginTop: '8px' }}>
            <span style={labelStyle}>Est. Net Worth</span>
            <span style={{ fontSize: '14px', fontWeight: 'bold' }}>{formatCurrency(netWorth)}</span>
          </div>
        </div>
      </div>

      {/* Primary Member Section */}
      <div>
        <h2 style={sectionHeaderStyle}>Primary Member</h2>
        <div style={grid3Style}>
          <div>
            <span style={labelStyle}>Full Name</span>
            <span style={valueStyle}>{primary.title} {primary.firstName} {primary.surname}</span>
          </div>
          <div>
            <span style={labelStyle}>ID / Passport</span>
            <span style={valueStyle}>{primary.idNumber || primary.passportNumber || '-'}</span>
          </div>
          <div>
            <span style={labelStyle}>Gender</span>
            <span style={valueStyle}>{primary.gender || '-'}</span>
          </div>
        </div>
        <div style={grid3Style}>
          <div>
            <span style={labelStyle}>Date of Birth</span>
            <span style={valueStyle}>{primary.dateOfBirth || '-'}</span>
          </div>
          <div>
            <span style={labelStyle}>Current Age</span>
            <span style={valueStyle}>{primary.currentAge || '-'}</span>
          </div>
          <div>
            <span style={labelStyle}>Next Birthday In</span>
            <span style={valueStyle}>{primary.daysToNextBirthday ? `${primary.daysToNextBirthday} days` : '-'}</span>
          </div>
        </div>
        <div style={grid3Style}>
          <div>
            <span style={labelStyle}>Age at Next Birthday</span>
            <span style={valueStyle}>{primary.ageNextBirthday || '-'}</span>
          </div>
          <div>
            <span style={labelStyle}>Birth Month</span>
            <span style={valueStyle}>{primary.dobMonthAbbr || '-'}</span>
          </div>
          <div>
            <span style={labelStyle}>Country of Birth</span>
            <span style={valueStyle}>{primary.countryOfBirth || (primary.idNumber ? 'South Africa' : '-')}</span>
          </div>
        </div>
      </div>

      {/* Relationship Status */}
      <div>
        <h2 style={sectionHeaderStyle}>Relationship Status</h2>
        <div style={grid2Style}>
          <div>
            <span style={labelStyle}>Status</span>
            <span style={valueStyle}>{relation.maritalStatus || '-'}</span>
          </div>
          {relation.partnershipDate && (
            <div>
              <span style={labelStyle}>Date of Marriage/Partnership</span>
              <span style={valueStyle}>{relation.partnershipDate}</span>
            </div>
          )}
        </div>
      </div>

      {/* Family Members */}
      {data.familyMembers && data.familyMembers.length > 0 && (
        <div>
          <h2 style={sectionHeaderStyle}>Family Members ({data.familyMembers.length})</h2>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                <th style={{ padding: '4px 4px 4px 0', ...labelStyle }}>Name</th>
                <th style={{ padding: '4px 4px', ...labelStyle }}>Relationship</th>
                <th style={{ padding: '4px 4px', ...labelStyle }}>ID / Passport</th>
                <th style={{ padding: '4px 4px', ...labelStyle }}>Age</th>
                <th style={{ padding: '4px 4px', ...labelStyle }}>Status</th>
                <th style={{ padding: '4px 0 4px 4px', ...labelStyle, textAlign: 'right' }}>Beneficiary</th>
              </tr>
            </thead>
            <tbody>
              {data.familyMembers.map((member: FamilyMember, index: number) => (
                <tr key={index} style={{ borderBottom: '1px solid #e5e7eb' }}>
                  <td style={{ padding: '8px 4px 8px 0', ...valueStyle }}>{member.title} {member.firstName} {member.surname}</td>
                  <td style={{ padding: '8px 4px', ...valueStyle }}>{member.relationship || '-'}</td>
                  <td style={{ padding: '8px 4px', ...valueStyle }}>{member.idNumber || member.passportNumber || '-'}</td>
                  <td style={{ padding: '8px 4px', ...valueStyle }}>{member.currentAge || '-'}</td>
                  <td style={{ padding: '8px 4px', ...valueStyle }}>
                    {[
                      member.financiallySupported && 'Supported',
                      member.isSupportive && 'Supportive'
                    ].filter(Boolean).join(', ') || '-'}
                  </td>
                  <td style={{ padding: '8px 0 8px 4px', ...valueStyle, textAlign: 'right' }}>
                    {member.isBeneficiary ? `${member.beneficiaryPercentage}%` : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Education Section */}
      {data.education && (
        <div>
          <h2 style={sectionHeaderStyle}>Education</h2>
          <div style={grid2Style}>
            <div>
              <span style={labelStyle}>Education Level</span>
              <span style={valueStyle}>{data.education.level || '-'}</span>
            </div>
            <div>
              <span style={labelStyle}>Institution</span>
              <span style={valueStyle}>{data.education.institution || '-'}</span>
            </div>
          </div>
        </div>
      )}

      {/* Financial Planning Context */}
      {data.financialPlanning && (
        <div>
          <h2 style={sectionHeaderStyle}>Financial Context</h2>

          <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
             <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Preferences</h3>
             <div style={grid2Style}>
               <div>
                 <span style={labelStyle}>Preferred Insurers</span>
                 <span style={valueStyle}>{data.financialPlanning.preferredInsurers || '-'}</span>
               </div>
               <div>
                 <span style={labelStyle}>Disliked Insurers</span>
                 <span style={valueStyle}>{data.financialPlanning.dislikedInsurers || '-'}</span>
               </div>
             </div>
             <div>
               <span style={labelStyle}>Plans to Adjust</span>
               <span style={valueStyle}>{data.financialPlanning.plannedChanges || '-'}</span>
             </div>
          </div>

          <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
             <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Financial Advisor History</h3>
             <div style={grid2Style}>
                <div>
                   <span style={labelStyle}>Previous Advisor?</span>
                   <span style={valueStyle}>{data.financialPlanning.hasFinancialAdvisor ? 'Yes' : 'No'}</span>
                </div>
                {data.financialPlanning.hasFinancialAdvisor && (
                   <div>
                      <span style={labelStyle}>Advisor Name/Firm</span>
                      <span style={valueStyle}>{data.financialPlanning.advisorName || '-'}</span>
                   </div>
                )}
             </div>
             {data.financialPlanning.advisorType && (
                <div style={{ marginTop: '8px' }}>
                   <span style={labelStyle}>Advisor Type</span>
                   <span style={valueStyle}>{data.financialPlanning.advisorType}</span>
                </div>
             )}
             {data.financialPlanning.lastReviewDate && (
                <div style={{ marginTop: '8px' }}>
                   <span style={labelStyle}>Last Review Date</span>
                   <span style={valueStyle}>{data.financialPlanning.lastReviewDate}</span>
                </div>
             )}
             {data.financialPlanning.lastContactDate && (
                <div style={{ marginTop: '8px' }}>
                   <span style={labelStyle}>Last Contact Date</span>
                   <span style={valueStyle}>{data.financialPlanning.lastContactDate}</span>
                </div>
             )}
             {data.financialPlanning.discontinuationReason && (
                <div style={{ marginTop: '8px' }}>
                   <span style={labelStyle}>Discontinuation Reason</span>
                   <span style={valueStyle}>{data.financialPlanning.discontinuationReason}</span>
                </div>
             )}
          </div>

          {/* Professional Relationships */}
          <div style={{ marginBottom: '16px' }}>
             <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Professional Relationships</h3>
             <div style={grid2Style}>
                <div>
                   <span style={labelStyle}>Has Accountant</span>
                   <span style={valueStyle}>{data.financialPlanning.hasAccountant ? 'Yes' : 'No'}</span>
                   {data.financialPlanning.hasAccountant && (
                      <div style={{ marginTop: '4px', fontSize: '10px' }}>
                         {data.financialPlanning.accountantServices?.taxPlanning && <span style={{ marginRight: '8px' }}>• Tax Planning</span>}
                         {data.financialPlanning.accountantServices?.investmentManagement && <span>• Investment Management</span>}
                      </div>
                   )}
                </div>
                <div>
                   <span style={labelStyle}>Has Attorney</span>
                   <span style={valueStyle}>{data.financialPlanning.hasAttorney ? 'Yes' : 'No'}</span>
                   {data.financialPlanning.hasAttorney && (
                      <div style={{ marginTop: '4px', fontSize: '10px' }}>
                         {data.financialPlanning.attorneyServices?.estatePlanning && <span>• Estate Planning</span>}
                      </div>
                   )}
                </div>
             </div>
          </div>
        </div>
      )}

      {/* Contact Details */}
      {data.contactDetails && (
        <div>
          <h2 style={sectionHeaderStyle}>Contact Details</h2>
          <div style={grid2Style}>
            <div>
              <span style={labelStyle}>Primary Address</span>
              <span style={valueStyle}>
                {[
                  data.contactDetails.primaryAddress?.street,
                  data.contactDetails.primaryAddress?.city,
                  data.contactDetails.primaryAddress?.province,
                  data.contactDetails.primaryAddress?.zipCode
                ].filter(Boolean).join(', ') || '-'}
              </span>
            </div>
            {data.contactDetails.mailingAddressDifferent && data.contactDetails.mailingAddress && (
              <div>
                <span style={labelStyle}>Mailing Address</span>
                <span style={valueStyle}>
                  {[
                    data.contactDetails.mailingAddress.street,
                    data.contactDetails.mailingAddress.city,
                    data.contactDetails.mailingAddress.province,
                    data.contactDetails.mailingAddress.zipCode
                  ].filter(Boolean).join(', ') || '-'}
                </span>
              </div>
            )}
          </div>
          <div style={grid2Style}>
            <div>
               <span style={labelStyle}>Phone (Primary)</span>
               <span style={valueStyle}>
                 {data.contactDetails.primaryPhone || '-'}
                 {data.contactDetails.primaryPhoneType && ` (${data.contactDetails.primaryPhoneType})`}
               </span>
             </div>
             <div>
               <span style={labelStyle}>Phone (Secondary)</span>
               <span style={valueStyle}>
                 {data.contactDetails.secondaryPhone || '-'}
                 {data.contactDetails.secondaryPhoneType && ` (${data.contactDetails.secondaryPhoneType})`}
               </span>
             </div>
          </div>
          <div style={grid2Style}>
             <div>
               <span style={labelStyle}>Email</span>
               <span style={valueStyle}>{data.contactDetails.email || '-'}</span>
             </div>
             <div>
               <span style={labelStyle}>WhatsApp</span>
               <span style={valueStyle}>{data.contactDetails.whatsapp || (data.contactDetails.whatsappSameAsPhone ? 'Same as Primary' : '-')}</span>
             </div>
          </div>

          {/* Insurance Portfolio Review */}
          <div style={{ marginTop: '16px', marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Insurance Portfolio Review</h3>
            <div style={grid3Style}>
              <div>
                <span style={labelStyle}>Life Insurance</span>
                <span style={valueStyle}>{formatCurrency(data.contactDetails.lifeInsuranceTotal)}</span>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Rec: {data.contactDetails.lifeInsuranceRecommend || '-'}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Target: {formatCurrency(data.contactDetails.lifeInsuranceTargetCover)}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Prem: {formatCurrency(data.contactDetails.lifeInsuranceTargetPremium)}</div>
              </div>
              <div>
                <span style={labelStyle}>Critical Illness</span>
                <span style={valueStyle}>{formatCurrency(data.contactDetails.criticalIllnessCoverage)}</span>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Rec: {data.contactDetails.criticalIllnessRecommend || '-'}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Target: {formatCurrency(data.contactDetails.criticalIllnessTargetCover)}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Prem: {formatCurrency(data.contactDetails.criticalIllnessTargetPremium)}</div>
              </div>
              <div>
                <span style={labelStyle}>Disability</span>
                <span style={valueStyle}>{data.contactDetails.disabilityCoverage || '-'} {data.contactDetails.disabilityCoverageType || ''}</span>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Rec: {data.contactDetails.disabilityCoverageRecommend || '-'}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Target: {formatCurrency(data.contactDetails.disabilityCoverageTargetCover)}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Prem: {formatCurrency(data.contactDetails.disabilityCoverageTargetPremium)}</div>
              </div>
            </div>
            <div style={grid3Style}>
              <div>
                <span style={labelStyle}>Income Protection</span>
                <span style={valueStyle}>{formatCurrency(data.contactDetails.incomeProtection)}</span>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Rec: {data.contactDetails.incomeProtectionRecommend || '-'}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Target: {formatCurrency(data.contactDetails.incomeProtectionTargetCover)}</div>
                <div style={{ fontSize: '9px', color: '#686a6c' }}>Prem: {formatCurrency(data.contactDetails.incomeProtectionTargetPremium)}</div>
              </div>
            </div>
          </div>

          {/* Health & Lifestyle */}
          <div style={{ marginTop: '16px', marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Health & Lifestyle</h3>
            <div style={grid2Style}>
              <div>
                <span style={labelStyle}>Height / Weight</span>
                <span style={valueStyle}>{data.contactDetails.height || '-'} / {data.contactDetails.weight || '-'}</span>
              </div>
              <div>
                <span style={labelStyle}>Criminal History</span>
                <span style={valueStyle}>{data.contactDetails.criminalHistory || '-'}</span>
              </div>
            </div>
            <div style={{ marginTop: '8px' }}>
              <span style={labelStyle}>Substance Use</span>
              <span style={valueStyle}>{data.contactDetails.substanceUse?.join(', ') || 'None'}</span>
              {data.contactDetails.substanceFrequency && <span style={{ fontSize: '9px', color: '#686a6c' }}> - {data.contactDetails.substanceFrequency}</span>}
            </div>
            <div style={{ marginTop: '8px' }}>
              <span style={labelStyle}>Hazardous Activities</span>
              <span style={valueStyle}>{data.contactDetails.hazardousActivities?.join(', ') || 'None'} {data.contactDetails.hazardousActivitiesOther ? `- ${data.contactDetails.hazardousActivitiesOther}` : ''}</span>
            </div>
            <div style={{ marginTop: '8px' }}>
              <span style={labelStyle}>Previous Insurance Application</span>
              <span style={valueStyle}>{data.contactDetails.appliedForInsurance ? 'Yes' : 'No'}</span>
              {data.contactDetails.declinedOrRated && <span style={{ fontSize: '9px', color: '#686a6c' }}> - {data.contactDetails.declinedOrRated}</span>}
            </div>
          </div>

          {/* Estate Planning */}
          <div style={{ marginTop: '16px', marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Estate Planning</h3>
            <div style={grid3Style}>
              <div>
                <span style={labelStyle}>Will Status</span>
                <span style={valueStyle}>{data.contactDetails.willStatus || '-'}</span>
                {data.contactDetails.willLastUpdated && <div style={{ fontSize: '9px', color: '#686a6c' }}>Updated: {data.contactDetails.willLastUpdated}</div>}
              </div>
              <div>
                <span style={labelStyle}>Trust Status</span>
                <span style={valueStyle}>{data.contactDetails.trustStatus || '-'}</span>
              </div>
              <div>
                <span style={labelStyle}>Inheritance</span>
                <span style={valueStyle}>{data.contactDetails.inheritanceExpectations || '-'}</span>
                {data.contactDetails.inheritanceAmount && <div style={{ fontSize: '9px', color: '#686a6c' }}>{formatCurrency(data.contactDetails.inheritanceAmount)}</div>}
              </div>
            </div>
          </div>

          {/* Financial Priorities */}
          <div style={{ marginTop: '16px', marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Financial Priorities</h3>
            <div style={{ marginBottom: '8px' }}>
              <span style={labelStyle}>Primary Concern</span>
              <span style={valueStyle}>{data.contactDetails.largestFinancialConcern || '-'}</span>
            </div>
            <div style={grid3Style}>
              <div><span style={labelStyle}>Emergency Fund</span><span style={valueStyle}>{data.contactDetails.priorityEmergencyFund || '-'}</span></div>
              <div><span style={labelStyle}>Debt Payoff</span><span style={valueStyle}>{data.contactDetails.priorityDebtPayoff || '-'}</span></div>
              <div><span style={labelStyle}>Retirement</span><span style={valueStyle}>{data.contactDetails.priorityRetirement || '-'}</span></div>
            </div>
            <div style={grid3Style}>
              <div><span style={labelStyle}>Insurance Coverage</span><span style={valueStyle}>{data.contactDetails.priorityInsuranceCoverage || '-'}</span></div>
              <div><span style={labelStyle}>Estate Preservation</span><span style={valueStyle}>{data.contactDetails.priorityEstatePreservation || '-'}</span></div>
              <div><span style={labelStyle}>Cash Flow</span><span style={valueStyle}>{data.contactDetails.priorityCashFlow || '-'}</span></div>
            </div>
            <div style={grid3Style}>
              <div><span style={labelStyle}>Income Replacement</span><span style={valueStyle}>{data.contactDetails.priorityIncomeReplacement || '-'}</span></div>
              <div><span style={labelStyle}>Education Funding</span><span style={valueStyle}>{data.contactDetails.priorityEducationFunding || '-'}</span></div>
              {data.contactDetails.incomeReplacementAmount && (
                <div><span style={labelStyle}>Target Amount</span><span style={valueStyle}>{formatCurrency(data.contactDetails.incomeReplacementAmount)}</span></div>
              )}
            </div>
          </div>

          {/* Risk & Implementation */}
          <div style={{ marginTop: '16px', marginBottom: '16px' }}>
            <div style={grid3Style}>
              <div><span style={labelStyle}>Budget</span><span style={valueStyle}>{data.contactDetails.budgetEstablished || '-'}</span></div>
              <div><span style={labelStyle}>Risk Tolerance</span><span style={valueStyle}>{data.contactDetails.riskTolerance || '-'}</span></div>
              <div><span style={labelStyle}>Implementation Barriers</span><span style={valueStyle}>{data.contactDetails.implementationBarriers || '-'}</span></div>
            </div>
            <div style={{ marginTop: '8px' }}>
              <span style={labelStyle}>Open to Immediate Action</span>
              <span style={valueStyle}>{data.contactDetails.openToImmediateAction ? 'Yes' : 'No'}</span>
            </div>
            {data.contactDetails.notes && (
              <div style={{ marginTop: '8px', padding: '8px', backgroundColor: '#f9fafb', borderRadius: '4px' }}>
                <span style={labelStyle}>Notes</span>
                <p style={{ fontSize: '11px', margin: '4px 0 0 0' }}>{data.contactDetails.notes}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Assets Section */}
      {(data.assets && data.assets.length > 0) && (
        <div>
          <h2 style={sectionHeaderStyle}>Assets & Investments</h2>
          
          {/* Summary */}
          <div style={{...grid3Style, backgroundColor: '#f9fafb', padding: '12px', border: '1px solid #e5e7eb'}}>
            <div>
              <span style={labelStyle}>Total Assets</span>
              <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(totalAssetValue)}</span>
            </div>
            <div>
              <span style={labelStyle}>Total Liabilities</span>
              <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(totalLiabilities)}</span>
            </div>
            <div>
              <span style={labelStyle}>Net Worth</span>
              <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(netWorth)}</span>
            </div>
          </div>

          {/* Physical Assets */}
          {data.assets.some(a => a.category === 'Physical') && (
            <div style={{ marginBottom: '24px' }}>
              <h3 style={{ fontSize: '12px', fontWeight: 'bold', borderBottom: '1px solid #e5e7eb', paddingBottom: '4px', marginBottom: '8px' }}>Properties & Physical Assets</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                    <th style={{ padding: '4px', ...labelStyle }}>Asset</th>
                    <th style={{ padding: '4px', ...labelStyle }}>Type</th>
                    <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Value</th>
                    <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Debt</th>
                    <th style={{ padding: '4px', ...labelStyle }}>Location</th>
                  </tr>
                </thead>
                <tbody>
                  {data.assets.filter(a => a.category === 'Physical').map((asset, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '6px 4px', ...valueStyle, fontWeight: 'bold' }}>{asset.name}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle }}>{asset.type}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(asset.currentValue || '0')}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(asset.remainingDebt || '0')}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle }}>{asset.location || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              
              {/* Detailed Asset Information */}
              {data.assets.filter(a => a.category === 'Physical').map((asset, i) => (
                <div key={i} style={{ marginTop: '12px', padding: '8px', backgroundColor: '#f9fafb', borderRadius: '4px' }}>
                  <div style={{ fontWeight: 'bold', fontSize: '11px', marginBottom: '6px' }}>{asset.name} - Details</div>
                  <div style={grid3Style}>
                    <div><span style={labelStyle}>Purchase Price</span><span style={valueStyle}>{formatCurrency(asset.purchasePrice)}</span></div>
                    <div><span style={labelStyle}>Purchase Date</span><span style={valueStyle}>{asset.purchaseDate || '-'}</span></div>
                    <div><span style={labelStyle}>Appreciation</span><span style={valueStyle}>{asset.appreciationRate ? `${asset.appreciationRate}%` : '-'}</span></div>
                  </div>
                  <div style={grid3Style}>
                    <div><span style={labelStyle}>Ownership</span><span style={valueStyle}>{asset.ownershipType || '-'}</span></div>
                    <div><span style={labelStyle}>Financed</span><span style={valueStyle}>{asset.isFinanced ? 'Yes' : 'No'}</span></div>
                    <div><span style={labelStyle}>Interest Rate</span><span style={valueStyle}>{asset.interestRate ? `${asset.interestRate}%` : '-'}</span></div>
                  </div>
                  {asset.isFinanced && (
                    <div style={grid3Style}>
                      <div><span style={labelStyle}>Amount Paid</span><span style={valueStyle}>{formatCurrency(asset.amountPaid)}</span></div>
                      <div><span style={labelStyle}>Monthly Repayment</span><span style={valueStyle}>{formatCurrency(asset.monthlyRepayment)}</span></div>
                    </div>
                  )}
                  <div style={grid3Style}>
                    <div><span style={labelStyle}>Monthly Income</span><span style={valueStyle}>{formatCurrency(asset.monthlyIncome)}</span></div>
                    <div><span style={labelStyle}>Monthly Cost</span><span style={valueStyle}>{formatCurrency(asset.monthlyCost)}</span></div>
                  </div>
                  {(asset.planToSell || asset.sellAtRetirement) && (
                    <div style={{ marginTop: '6px', padding: '6px', backgroundColor: '#fef3c7' }}>
                      <div style={grid3Style}>
                        <div><span style={labelStyle}>Plan to Sell</span><span style={valueStyle}>{asset.planToSell ? 'Yes' : 'No'}</span></div>
                        <div><span style={labelStyle}>Target Year</span><span style={valueStyle}>{asset.targetSellYear || '-'}</span></div>
                        <div><span style={labelStyle}>Expected Price</span><span style={valueStyle}>{formatCurrency(asset.expectedSellPrice)}</span></div>
                      </div>
                      <div style={grid2Style}>
                        <div><span style={labelStyle}>Sell at Retirement</span><span style={valueStyle}>{asset.sellAtRetirement || '-'}</span></div>
                        <div><span style={labelStyle}>Settle Debt on Death</span><span style={valueStyle}>{asset.settleDebtOnDeath ? 'Yes' : 'No'}</span></div>
                      </div>
                      {asset.estatePlan && (
                        <div style={{ marginTop: '4px' }}>
                          <span style={labelStyle}>Estate Plan</span>
                          <span style={valueStyle}>{asset.estatePlan}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Financial Assets */}
          {data.assets.some(a => a.category === 'Financial') && (
            <div>
              <h3 style={{ fontSize: '12px', fontWeight: 'bold', borderBottom: '1px solid #e5e7eb', paddingBottom: '4px', marginBottom: '8px' }}>Investments & Savings</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                    <th style={{ padding: '4px', ...labelStyle }}>Type</th>
                    <th style={{ padding: '4px', ...labelStyle }}>Institution / Product</th>
                    <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Balance</th>
                    <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Return %</th>
                    <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Monthly Contrib.</th>
                  </tr>
                </thead>
                <tbody>
                  {data.assets.filter(a => a.category === 'Financial').map((asset, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '6px 4px', ...valueStyle, fontWeight: 'bold' }}>{asset.type}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle }}>{asset.companyName || '-'}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(asset.currentBalance || '0')}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{asset.projectedReturn ? `${asset.projectedReturn}%` : '-'}</td>
                      <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(asset.monthlyContribution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Liabilities Section */}
      {(data.liabilities && data.liabilities.length > 0) && (
        <div style={{ marginTop: '24px' }}>
          <h2 style={sectionHeaderStyle}>Liabilities & Debt</h2>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                <th style={{ padding: '4px', ...labelStyle }}>Type</th>
                <th style={{ padding: '4px', ...labelStyle }}>Lender / Name</th>
                <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Balance</th>
                <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Monthly Payment</th>
                <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Interest Rate</th>
                <th style={{ padding: '4px', ...labelStyle }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.liabilities.map((liability, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #e5e7eb' }}>
                  <td style={{ padding: '6px 4px', ...valueStyle, fontWeight: 'bold' }}>{liability.type}</td>
                  <td style={{ padding: '6px 4px', ...valueStyle }}>{liability.name || '-'}</td>
                  <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(liability.currentBalance || '0')}</td>
                  <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(liability.monthlyPayment || '0')}</td>
                  <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{liability.interestRate ? `${liability.interestRate}%` : '-'}</td>
                  <td style={{ padding: '6px 4px', ...valueStyle }}>{liability.status || (liability.earlyPayoff ? 'Early Payoff' : 'Current')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          
          {/* Additional Liability Details */}
          <div style={{ marginTop: '16px' }}>
            {data.liabilities.map((liability, i) => (
              <div key={i} style={{ marginBottom: '12px', padding: '8px', backgroundColor: '#f9fafb', borderRadius: '4px' }}>
                <div style={{ fontWeight: 'bold', fontSize: '11px', marginBottom: '4px' }}>{liability.name} ({liability.type})</div>
                <div style={grid3Style}>
                  <div><span style={labelStyle}>Original Amount</span><span style={valueStyle}>{formatCurrency(liability.originalAmount)}</span></div>
                  <div><span style={labelStyle}>Term</span><span style={valueStyle}>{liability.term} {liability.termUnit}</span></div>
                  <div><span style={labelStyle}>Secured</span><span style={valueStyle}>{liability.isSecured ? 'Yes' : 'No'}</span></div>
                </div>
                {liability.targetPayoffDate && (
                  <div style={{ marginTop: '4px' }}>
                    <span style={labelStyle}>Target Payoff Date</span>
                    <span style={valueStyle}>{liability.targetPayoffDate}</span>
                  </div>
                )}
                {liability.isConsolidated && (
                  <div style={{ marginTop: '4px' }}>
                    <span style={labelStyle}>Consolidation Status</span>
                    <span style={valueStyle}>{liability.isConsolidated}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Retirement Section - COMPREHENSIVE */}
      {data.retirement && (
        <div style={{ marginTop: '24px' }}>
          <h2 style={sectionHeaderStyle}>Retirement Planning</h2>
          
          {/* Dashboard */}
          <div style={{...grid3Style, backgroundColor: '#f0fdf4', padding: '12px', border: '1px solid #bbf7d0'}}>
             <div>
                <span style={labelStyle}>Total Savings</span>
                <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(totalRetirementSavings)}</span>
             </div>
             <div>
                <span style={labelStyle}>Potential Monthly Income</span>
                <span style={valueStyle}>{formatCurrency(currentPotentialMonthlyIncome)}</span>
                <div style={{ fontSize: '8px', color: '#686a6c' }}>(Net Worth × 0.8%)</div>
             </div>
             <div>
                <span style={labelStyle}>Earliest Ret. Age</span>
                <span style={{...valueStyle, fontWeight: 'bold', color: '#1e3a8a'}}>
                    {earliestAge ? `${earliestAge.toFixed(1)} Years` : 'N/A'}
                </span>
             </div>
          </div>

          <div style={grid3Style}>
             <div>
                <span style={labelStyle}>Desired Age</span>
                <span style={valueStyle}>{data.retirement.desiredRetirementAge || '65'}</span>
             </div>
             <div>
                <span style={labelStyle}>Target Monthly Income</span>
                <span style={valueStyle}>{formatCurrency(data.retirement.targetMonthlyIncome || '0')}</span>
             </div>
             <div>
                <span style={labelStyle}>Simultaneous Ret.</span>
                <span style={valueStyle}>{data.retirement.simultaneousRetirement ? 'Yes' : 'No'}</span>
             </div>
          </div>

          {/* Contributions */}
          <div style={{ marginBottom: '16px', padding: '12px', backgroundColor: '#f9fafb' }}>
             <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Monthly Contributions</h3>
             <div style={grid3Style}>
                <div>
                   <span style={labelStyle}>Employer ({data.retirement.employerMatchPercentage || '0'}%)</span>
                   <span style={valueStyle}>{formatCurrency(data.retirement.employerContribution || '0')}</span>
                </div>
                <div>
                   <span style={labelStyle}>Own Contribution</span>
                   <span style={valueStyle}>{formatCurrency(data.retirement.employeeContribution || '0')}</span>
                </div>
                <div>
                   <span style={labelStyle}>Total</span>
                   <span style={{...valueStyle, fontWeight: 'bold'}}>
                     {formatCurrency((parseFloat(data.retirement.employerContribution || '0') + parseFloat(data.retirement.employeeContribution || '0')))}
                   </span>
                </div>
             </div>
          </div>

          {/* Analysis */}
          <div style={{ marginBottom: '16px' }}>
             <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Analysis (1/3 vs 2/3)</h3>
             <div style={grid2Style}>
                <div>
                   <span style={labelStyle}>1/3 Lump Sum</span>
                   <span style={valueStyle}>{formatCurrency(oneThirdLumpSum)}</span>
                   <div style={{ fontSize: '9px', color: '#dc2626' }}>Est. Tax: {formatCurrency(taxOnLumpSum)}</div>
                </div>
                <div>
                   <span style={labelStyle}>2/3 Annuity</span>
                   <span style={valueStyle}>{formatCurrency(totalRetirementSavings * (2/3))}</span>
                </div>
             </div>
          </div>

          {/* Funds Table */}
          {data.retirement.retirementFunds?.length > 0 && (
            <div style={{ marginBottom: '16px' }}>
                <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Retirement Funds</h3>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                      <th style={{ padding: '4px', ...labelStyle }}>Company</th>
                      <th style={{ padding: '4px', ...labelStyle }}>Product</th>
                      <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Balance</th>
                      <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Growth %</th>
                      <th style={{ padding: '4px', ...labelStyle }}>Employee Benefit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.retirement.retirementFunds.map((fund, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #e5e7eb' }}>
                        <td style={{ padding: '6px 4px', ...valueStyle }}>{fund.companyName}</td>
                        <td style={{ padding: '6px 4px', ...valueStyle }}>{fund.productName}</td>
                        <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(fund.currentBalance)}</td>
                        <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{fund.growthRate}%</td>
                        <td style={{ padding: '6px 4px', ...valueStyle }}>{fund.isEmployeeBenefit === 'true' ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
            </div>
          )}

          {/* Other Income & Annuities */}
          {(data.retirement.otherIncomeSources?.length > 0 || data.retirement.activeAnnuities?.length > 0) && (
            <div style={{ marginBottom: '16px' }}>
               <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Additional Retirement Income</h3>
               <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #000000', textAlign: 'left' }}>
                      <th style={{ padding: '4px', ...labelStyle }}>Type</th>
                      <th style={{ padding: '4px', ...labelStyle }}>Source</th>
                      <th style={{ padding: '4px', ...labelStyle, textAlign: 'right' }}>Monthly Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.retirement.otherIncomeSources?.map((item, i) => (
                      <tr key={`other-${i}`} style={{ borderBottom: '1px solid #e5e7eb' }}>
                         <td style={{ padding: '6px 4px', ...valueStyle }}>Future Income</td>
                         <td style={{ padding: '6px 4px', ...valueStyle }}>{item.source}</td>
                         <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(item.monthlyAmount)}</td>
                      </tr>
                    ))}
                    {data.retirement.activeAnnuities?.map((item, i) => (
                      <tr key={`annuity-${i}`} style={{ borderBottom: '1px solid #e5e7eb' }}>
                         <td style={{ padding: '6px 4px', ...valueStyle }}>Active Annuity</td>
                         <td style={{ padding: '6px 4px', ...valueStyle }}>{item.source}</td>
                         <td style={{ padding: '6px 4px', ...valueStyle, textAlign: 'right' }}>{formatCurrency(item.monthlyAmount)}</td>
                      </tr>
                    ))}
                  </tbody>
               </table>
            </div>
          )}

          {/* Retirement Strategies */}
          <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#fef3c7', border: '1px solid #fcd34d' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#92400e', marginBottom: '12px' }}>Retirement Strategies</h3>
            <div style={grid2Style}>
              <div>
                <span style={labelStyle}>Optimization Strategy</span>
                <span style={valueStyle}>{data.retirement.optimizationStrategy || 'None Selected'}</span>
              </div>
              <div>
                <span style={labelStyle}>Simultaneous Retirement</span>
                <span style={valueStyle}>{data.retirement.simultaneousRetirement ? 'Yes' : 'No'}</span>
              </div>
            </div>
            {data.retirement.payoffLiabilities && data.retirement.payoffLiabilities.length > 0 && (
              <div style={{ marginTop: '8px' }}>
                <span style={labelStyle}>Liabilities to Payoff at Retirement</span>
                <div style={{ fontSize: '10px', marginTop: '4px' }}>
                  {data.retirement.payoffLiabilities.map((id, i) => (
                    <span key={i} style={{ marginRight: '12px' }}>• {id}</span>
                  ))}
                </div>
              </div>
            )}
            {data.retirement.assetsToSell && data.retirement.assetsToSell.length > 0 && (
              <div style={{ marginTop: '8px' }}>
                <span style={labelStyle}>Assets to Sell at Retirement</span>
                <div style={{ fontSize: '10px', marginTop: '4px' }}>
                  {data.retirement.assetsToSell.map((id, i) => (
                    <span key={i} style={{ marginRight: '12px' }}>• {id}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Goals & Emergency Fund Section */}
      {(data.goals?.length > 0 || data.emergencyFund) && (
        <div style={{ marginTop: '24px', pageBreakBefore: 'always' }}>
          <h2 style={sectionHeaderStyle}>Goals & Preparedness</h2>
          
          {/* Emergency Fund */}
          {data.emergencyFund && (
             <div style={{ marginBottom: '24px', padding: '12px', backgroundColor: '#eff6ff', border: '1px solid #bfdbfe' }}>
                <h3 style={{ ...labelStyle, fontSize: '11px', color: '#1e40af', marginBottom: '12px' }}>Emergency Fund</h3>
                <div style={grid3Style}>
                   <div>
                      <span style={labelStyle}>Target Months</span>
                      <span style={valueStyle}>{data.emergencyFund.targetMonths || '-'}</span>
                   </div>
                   <div>
                      <span style={labelStyle}>Current Savings</span>
                      <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(data.emergencyFund.currentSavings)}</span>
                   </div>
                   <div>
                      <span style={labelStyle}>Monthly Contrib.</span>
                      <span style={valueStyle}>{formatCurrency(data.emergencyFund.monthlyContribution)}</span>
                   </div>
                </div>
             </div>
          )}

          {/* Goals List */}
          {data.goals?.length > 0 && (
             <div>
                <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '12px' }}>Financial Goals</h3>
                {data.goals.map((goal, i) => (
                   <div key={i} style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                         <span style={{ fontWeight: 'bold', fontSize: '13px' }}>{i + 1}. {goal.name} ({goal.category})</span>
                         <span style={{ fontSize: '10px', backgroundColor: '#f3f4f6', padding: '2px 6px', borderRadius: '4px' }}>{goal.type}</span>
                      </div>
                      
                      <div style={grid3Style}>
                         <div>
                            <span style={labelStyle}>Target Date/Age</span>
                            <span style={valueStyle}>{goal.targetDate || (goal.targetAge ? `Age ${goal.targetAge}` : '-')}</span>
                         </div>
                         <div>
                            <span style={labelStyle}>Current Cost</span>
                            <span style={valueStyle}>{formatCurrency(goal.currentCost)}</span>
                         </div>
                         <div>
                            <span style={labelStyle}>Lump Sum Saved</span>
                            <span style={{...valueStyle, fontWeight: 'bold', color: '#166534'}}>{formatCurrency(goal.lumpSumSaved)}</span>
                         </div>
                      </div>

                      {goal.financingRequired ? (
                          <div style={{ marginTop: '8px', padding: '8px', backgroundColor: '#fff7ed', borderRadius: '4px' }}>
                             <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#9a3412', marginBottom: '4px' }}>FINANCING REQUIRED</div>
                             <div style={grid3Style}>
                                <div>
                                   <span style={labelStyle}>Deposit Only</span>
                                   <span style={valueStyle}>{goal.saveDepositOnly ? 'Yes' : 'No'}</span>
                                </div>
                                <div>
                                   <span style={labelStyle}>Deposit</span>
                                   <span style={valueStyle}>{formatCurrency(goal.depositAmount)}</span>
                                </div>
                                <div>
                                   <span style={labelStyle}>Loan Term</span>
                                   <span style={valueStyle}>{goal.loanTerm} Months</span>
                                </div>
                                <div>
                                   <span style={labelStyle}>Interest Rate</span>
                                   <span style={valueStyle}>{goal.interestRate}%</span>
                                </div>
                             </div>
                          </div>
                      ) : (
                          <div style={grid2Style}>
                             <div>
                                <span style={labelStyle}>Monthly Contrib. (Current)</span>
                                <span style={valueStyle}>{formatCurrency(goal.monthlyContributionCurrent)}</span>
                             </div>
                             {goal.ongoingExpenses && (
                               <div>
                                  <span style={labelStyle}>Ongoing Expenses</span>
                                  <span style={valueStyle}>{formatCurrency(goal.ongoingExpenses)}</span>
                               </div>
                             )}
                          </div>
                      )}
                   </div>
                ))}
             </div>
          )}
        </div>
      )}

      {/* Employment & Income */}
      {data.employment && (
        <div style={{ marginTop: '24px' }}>
          <h2 style={sectionHeaderStyle}>Employment & Income</h2>

          <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Primary Employment</h3>
            <div style={grid3Style}>
               <div>
                  <span style={labelStyle}>Status</span>
                  <span style={valueStyle}>{data.employment.employmentStatus}</span>
               </div>
               <div>
                  <span style={labelStyle}>Industry</span>
                  <span style={valueStyle}>{data.employment.industry || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Job Title</span>
                  <span style={valueStyle}>{data.employment.jobTitle || '-'}</span>
               </div>
            </div>
            <div style={grid3Style}>
               <div>
                  <span style={labelStyle}>Primary Source</span>
                  <span style={valueStyle}>{data.employment.primarySource || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Employer</span>
                  <span style={valueStyle}>{data.employment.employer || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Occupation</span>
                  <span style={valueStyle}>{data.employment.occupation || '-'}</span>
               </div>
            </div>
            <div style={grid3Style}>
               <div>
                  <span style={labelStyle}>Tenure (Years)</span>
                  <span style={valueStyle}>{data.employment.tenure || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Years in Workforce</span>
                  <span style={valueStyle}>{data.employment.yearsInWorkforce || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Medical Members</span>
                  <span style={valueStyle}>{data.employment.medicalMembers || '-'}</span>
               </div>
            </div>
            <div style={grid3Style}>
               <div>
                  <span style={labelStyle}>Monthly Gross</span>
                  <span style={{...valueStyle, fontWeight: 'bold'}}>{formatCurrency(data.employment.primaryIncomeAmount)}</span>
               </div>
               <div>
                  <span style={labelStyle}>Annual Gross</span>
                  <span style={valueStyle}>{formatCurrency((parseFloat(data.employment.primaryIncomeAmount) || 0) * 12)}</span>
               </div>
               <div>
                  <span style={labelStyle}>Pays Tax</span>
                  <span style={valueStyle}>{data.employment.paysTax ? 'Yes' : 'No'}</span>
               </div>
            </div>
            <div style={grid3Style}>
               <div>
                  <span style={labelStyle}>Salary Date</span>
                  <span style={valueStyle}>{data.employment.salaryDate || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>Debit Order Date</span>
                  <span style={valueStyle}>{data.employment.debitOrderDate || '-'}</span>
               </div>
               <div>
                  <span style={labelStyle}>RA Contribution</span>
                  <span style={valueStyle}>{formatCurrency(data.employment.retirementContribution)}</span>
               </div>
            </div>
          </div>

          {/* Other Income Sources */}
          {data.employment.otherSources && data.employment.otherSources.length > 0 && (
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Other Income Sources</h3>
              {data.employment.otherSources.map((source, i) => (
                <div key={i} style={grid2Style}>
                  <div><span style={labelStyle}>Source</span><span style={valueStyle}>{source.source}</span></div>
                  <div><span style={labelStyle}>Monthly Amount</span><span style={valueStyle}>{formatCurrency(source.amount)}</span></div>
                </div>
              ))}
            </div>
          )}

          {/* Partner Income */}
          {data.employment.partnerSources && data.employment.partnerSources.length > 0 && (
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Partner Income</h3>
              {data.employment.partnerSources.map((source, i) => (
                <div key={i} style={grid2Style}>
                  <div><span style={labelStyle}>Source</span><span style={valueStyle}>{source.source}</span></div>
                  <div><span style={labelStyle}>Monthly Amount</span><span style={valueStyle}>{formatCurrency(source.amount)}</span></div>
                </div>
              ))}
            </div>
          )}

          {/* Employment Breaks */}
          {data.employment.employmentBreaks && data.employment.employmentBreaks.length > 0 && (
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Significant Employment Breaks</h3>
              {data.employment.employmentBreaks.map((breakItem, i) => (
                <div key={i} style={grid2Style}>
                  <div><span style={labelStyle}>Reason</span><span style={valueStyle}>{breakItem.reason}</span></div>
                  <div><span style={labelStyle}>Duration</span><span style={valueStyle}>{breakItem.duration} months</span></div>
                </div>
              ))}
            </div>
          )}

          {/* Career Plans */}
          {data.employment.careerPlans?.plannedChange && (
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Career Plans</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Planned Change</span><span style={valueStyle}>{data.employment.careerPlans.plannedChange}</span></div>
                <div><span style={labelStyle}>Potential Salary</span><span style={valueStyle}>{formatCurrency(data.employment.careerPlans.potentialSalary)}</span></div>
              </div>
              {data.employment.careerPlans.notes && (
                <div style={{ marginTop: '8px' }}>
                  <span style={labelStyle}>Notes</span>
                  <p style={{ fontSize: '11px', margin: '4px 0 0 0' }}>{data.employment.careerPlans.notes}</p>
                </div>
              )}
            </div>
          )}

          {/* Future Income */}
          {data.employment.futureIncome && data.employment.futureIncome.length > 0 && (
            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Expected Future Income</h3>
              {data.employment.futureIncome.map((item, i) => (
                <div key={i} style={grid3Style}>
                  <div><span style={labelStyle}>Source</span><span style={valueStyle}>{item.source}</span></div>
                  <div><span style={labelStyle}>Amount</span><span style={valueStyle}>{formatCurrency(item.amount)}</span></div>
                  <div><span style={labelStyle}>Start Age</span><span style={valueStyle}>{item.startAge}</span></div>
                </div>
              ))}
            </div>
          )}

          {/* Employee Benefits */}
          {data.employment.hasEmployeeBenefits && (
            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#f0fdf4', borderRadius: '4px' }}>
              <span style={labelStyle}>Employee Benefits</span>
              <p style={{ fontSize: '11px', margin: '4px 0 0 0' }}>{data.employment.employeeBenefitsNotes || 'No details provided'}</p>
            </div>
          )}

          {/* Contribution Increases */}
          <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#fef3c7', borderRadius: '4px' }}>
            <h3 style={{ ...labelStyle, fontSize: '11px', color: '#92400e', marginBottom: '8px' }}>Annual Contribution Increases</h3>
            <div style={grid2Style}>
              <div>
                <span style={labelStyle}>Employer Increase</span>
                <span style={valueStyle}>{data.employment.employerContributionIncrease ? `${data.employment.employerContributionIncrease}%` : '-'}</span>
              </div>
              <div>
                <span style={labelStyle}>Employee Increase</span>
                <span style={valueStyle}>{data.employment.employeeContributionIncrease ? `${data.employment.employeeContributionIncrease}%` : '-'}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Expenses Section */}
      {data.expenses && (
        <div style={{ marginTop: '24px', pageBreakBefore: 'always' }}>
            <h2 style={sectionHeaderStyle}>Monthly Expenses & Budget</h2>
            
            {/* Transport */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Transport</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Mode</span><span style={valueStyle}>{expenses.transportMode || '-'}</span></div>
                <div><span style={labelStyle}>Fuel</span><span style={valueStyle}>{formatCurrency(expenses.fuelCost)}</span></div>
                <div><span style={labelStyle}>Auto Insurance</span><span style={valueStyle}>{formatCurrency(expenses.autoInsurance)}</span></div>
              </div>
              {expenses.autoInsurancePolicy && (
                <div style={{ marginTop: '8px' }}>
                  <span style={labelStyle}>Auto Insurance Policy</span>
                  <span style={valueStyle}>{expenses.autoInsurancePolicy}</span>
                </div>
              )}
              <div style={grid3Style}>
                <div><span style={labelStyle}>Vehicle Repayment</span><span style={valueStyle}>{formatCurrency(expenses.vehicleRepayment)}</span></div>
                <div><span style={labelStyle}>Maintenance Plan</span><span style={valueStyle}>{expenses.hasMaintenancePlan ? 'Yes' : 'No'}</span></div>
                <div><span style={labelStyle}>Maintenance Cost</span><span style={valueStyle}>{formatCurrency(expenses.maintenanceCost)}</span></div>
              </div>
              <div style={{ marginTop: '8px' }}>
                <span style={labelStyle}>Tolls & Tracker</span>
                <span style={valueStyle}>{formatCurrency(expenses.tollsAndTracker)}</span>
              </div>
            </div>

            {/* Living Situation */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Housing</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Situation</span><span style={valueStyle}>{expenses.livingSituation || '-'}</span></div>
                <div><span style={labelStyle}>Time at Address</span><span style={valueStyle}>{expenses.timeAtAddress || '-'}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Mortgage/Rent</span><span style={valueStyle}>{formatCurrency(expenses.mortgagePayment || expenses.rentPayment)}</span></div>
                <div><span style={labelStyle}>Rates & Taxes</span><span style={valueStyle}>{formatCurrency(expenses.ratesAndTaxes)}</span></div>
                <div><span style={labelStyle}>Household Insurance</span><span style={valueStyle}>{formatCurrency(expenses.householdInsurance)}</span></div>
              </div>
              {expenses.householdInsurancePolicy && (
                <div style={{ marginTop: '8px' }}>
                  <span style={labelStyle}>Household Insurance Policy</span>
                  <span style={valueStyle}>{expenses.householdInsurancePolicy}</span>
                </div>
              )}
              <div style={grid3Style}>
                <div><span style={labelStyle}>Electricity</span><span style={valueStyle}>{formatCurrency(expenses.electricity)}</span></div>
                <div><span style={labelStyle}>Water</span><span style={valueStyle}>{formatCurrency(expenses.water)}</span></div>
                <div><span style={labelStyle}>Gas</span><span style={valueStyle}>{formatCurrency(expenses.gas)}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Internet</span><span style={valueStyle}>{formatCurrency(expenses.internet)}</span></div>
                <div><span style={labelStyle}>DSTV</span><span style={valueStyle}>{formatCurrency(expenses.dstv)}</span></div>
                <div><span style={labelStyle}>Trash Collection</span><span style={valueStyle}>{formatCurrency(expenses.trashCollection)}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Cleaning Services</span><span style={valueStyle}>{formatCurrency(expenses.cleaningServices)}</span></div>
                <div><span style={labelStyle}>Landscaping</span><span style={valueStyle}>{formatCurrency(expenses.landscaping)}</span></div>
                <div><span style={labelStyle}>Other Household</span><span style={valueStyle}>{formatCurrency(expenses.otherHouseholdCosts)}</span></div>
              </div>
            </div>

            {/* Food & Personal */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Food & Personal</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Groceries</span><span style={valueStyle}>{formatCurrency(expenses.groceries)}</span></div>
                <div><span style={labelStyle}>Dining Out</span><span style={valueStyle}>{formatCurrency(expenses.diningOut)}</span></div>
                <div><span style={labelStyle}>Clothing</span><span style={valueStyle}>{formatCurrency(expenses.clothing)}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Personal Care</span><span style={valueStyle}>{formatCurrency(expenses.personalCare)}</span></div>
                <div><span style={labelStyle}>Pet Care</span><span style={valueStyle}>{formatCurrency(expenses.petCare)}</span></div>
                <div><span style={labelStyle}>Hobbies</span><span style={valueStyle}>{formatCurrency(expenses.hobbies)}</span></div>
              </div>
            </div>

            {/* Subscriptions */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Subscriptions</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Gym</span><span style={valueStyle}>{formatCurrency(expenses.subscriptions?.gym)}</span></div>
                <div><span style={labelStyle}>Netflix</span><span style={valueStyle}>{formatCurrency(expenses.subscriptions?.netflix)}</span></div>
                <div><span style={labelStyle}>YouTube</span><span style={valueStyle}>{formatCurrency(expenses.subscriptions?.youtube)}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Spotify</span><span style={valueStyle}>{formatCurrency(expenses.subscriptions?.spotify)}</span></div>
                <div><span style={labelStyle}>Other Subs</span><span style={valueStyle}>{formatCurrency(expenses.subscriptions?.other)}</span></div>
                <div><span style={labelStyle}>Cellphone & Data</span><span style={valueStyle}>{formatCurrency(expenses.cellphoneData)}</span></div>
              </div>
            </div>

            {/* Medical */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Medical</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>On Family Plan</span><span style={valueStyle}>{expenses.onFamilyPlan ? 'Yes' : 'No'}</span></div>
                <div><span style={labelStyle}>Open to Options</span><span style={valueStyle}>{expenses.openToMedicalOptions ? 'Yes' : 'No'}</span></div>
                <div><span style={labelStyle}>Medical Aid Premium</span><span style={valueStyle}>{formatCurrency(expenses.medicalAidPremium)}</span></div>
              </div>
              {expenses.medicalAidPlan && (
                <div style={{ marginTop: '8px' }}>
                  <span style={labelStyle}>Medical Aid Plan</span>
                  <span style={valueStyle}>{expenses.medicalAidPlan}</span>
                </div>
              )}
              <div style={{ marginTop: '8px', padding: '8px', backgroundColor: '#f0fdf4' }}>
                <h4 style={{ fontSize: '10px', fontWeight: 'bold', color: '#047857', marginBottom: '4px' }}>Gap Cover</h4>
                <div style={grid3Style}>
                  <div><span style={labelStyle}>Plan</span><span style={valueStyle}>{expenses.gapCoverPlan || '-'}</span></div>
                  <div><span style={labelStyle}>Premium</span><span style={valueStyle}>{formatCurrency(expenses.gapCoverPremium)}</span></div>
                  <div><span style={labelStyle}>Open to Options</span><span style={valueStyle}>{expenses.openToGapOptions ? 'Yes' : 'No'}</span></div>
                </div>
              </div>
              <div style={{ marginTop: '8px' }}>
                <span style={labelStyle}>Other Medical Costs</span>
                <span style={valueStyle}>{formatCurrency(expenses.otherMedicalCosts)}</span>
              </div>
            </div>

            {/* Children & Education */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Children & Education</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Child Care</span><span style={valueStyle}>{formatCurrency(expenses.childCare)}</span></div>
                <div><span style={labelStyle}>Annual Education</span><span style={valueStyle}>{formatCurrency(expenses.childEducationAnnual)}</span></div>
                <div><span style={labelStyle}>Educational Expenses</span><span style={valueStyle}>{formatCurrency(expenses.educationalExpenses)}</span></div>
              </div>
              <div style={{ marginTop: '8px' }}>
                <span style={labelStyle}>Child Support</span>
                <span style={valueStyle}>{formatCurrency(expenses.childSupport)}</span>
              </div>
            </div>

            {/* Debt Payments */}
            <div style={{ marginBottom: '16px', borderBottom: '1px solid #e5e7eb', paddingBottom: '12px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Debt Payments</h3>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Has Credit Card</span><span style={valueStyle}>{expenses.hasCreditCard ? 'Yes' : 'No'}</span></div>
                <div><span style={labelStyle}>Credit Card Payments</span><span style={valueStyle}>{formatCurrency(expenses.creditCardPayments)}</span></div>
                <div><span style={labelStyle}>Personal Loans</span><span style={valueStyle}>{formatCurrency(expenses.personalLoans)}</span></div>
              </div>
              <div style={grid3Style}>
                <div><span style={labelStyle}>Student Loans</span><span style={valueStyle}>{formatCurrency(expenses.studentLoans)}</span></div>
                <div><span style={labelStyle}>Other Debt</span><span style={valueStyle}>{formatCurrency(expenses.otherDebt)}</span></div>
                <div><span style={labelStyle}>Child Support</span><span style={valueStyle}>{formatCurrency(expenses.childSupport)}</span></div>
              </div>
            </div>

            {/* Giving */}
            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Giving</h3>
              <div style={grid2Style}>
                <div><span style={labelStyle}>Gifts & Donations</span><span style={valueStyle}>{formatCurrency(expenses.giftsDonations)}</span></div>
                <div><span style={labelStyle}>Charitable Donations</span><span style={valueStyle}>{formatCurrency(expenses.charitableDonations)}</span></div>
              </div>
            </div>

            {/* Travel */}
            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ ...labelStyle, fontSize: '11px', color: '#000000', marginBottom: '8px' }}>Travel Budget</h3>
              <div><span style={valueStyle}>{formatCurrency(expenses.travelBudget)}</span></div>
            </div>
        </div>
      )}

      {/* Footer */}
      <div style={{ marginTop: '60px', paddingTop: '12px', borderTop: '1px solid #000000', display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#686a6c' }}>
        <span>CONFIDENTIAL - INTERNAL USE ONLY</span>
        <span>GENERATED BY AUTOMATED SYSTEM</span>
      </div>
    </div>
  );
};

export default PDFTemplate;
