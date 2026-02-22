'use client';

import React, { useState, useEffect, useRef } from 'react';
import MemberForm from './MemberForm';
import ContactDetails from './ContactDetails';
import RelationshipStatus from './RelationshipStatus';
import FinancialPlanningSection from './FinancialPlanningSection';
import EducationSection from './EducationSection';
import EmploymentSection from './EmploymentSection';
import ExpensesSection from './ExpensesSection';
import AssetsSection from './AssetsSection';
import LiabilitiesSection from './LiabilitiesSection';
import RetirementSection from './RetirementSection';
import GoalsSection from './GoalsSection';
import SectionNavigation from './SectionNavigation';
import { FormData, IncomeSource, Asset, Goal, EmergencyFund } from '../types';

interface ClientFile {
  filename: string;
  name: string;
  lastModified: string;
}

const SECTIONS = [
  { id: 'primary-member', label: 'Primary Member' },
  { id: 'relationship', label: 'Relationship Status' },
  { id: 'family', label: 'Family Members' },
  { id: 'financial-planning', label: 'Financial Planning' },
  { id: 'contact', label: 'Contact Details' },
  { id: 'education', label: 'Education' },
  { id: 'employment', label: 'Employment' },
  { id: 'expenses', label: 'Expenses' },
  { id: 'assets', label: 'Assets' },
  { id: 'liabilities', label: 'Liabilities' },
  { id: 'retirement', label: 'Retirement' },
  { id: 'goals', label: 'Goals' },
];

const initialFormData: FormData = {
  clientName: '',
  primaryMember: {
    title: '', firstName: '', surname: '', idNumber: '', passportNumber: '', countryOfBirth: '', 
    gender: '', dateOfBirth: '', currentAge: '', ageNextBirthday: '', daysToNextBirthday: ''
  },
  familyMembers: [],
  contactDetails: {
    primaryAddress: { street: '', city: '', province: '', zipCode: '' },
    mailingAddress: { street: '', city: '', province: '', zipCode: '' },
    mailingAddressDifferent: false,
    primaryPhone: '', primaryPhoneType: 'Mobile',
    secondaryPhone: '', secondaryPhoneType: 'Mobile',
    email: '', whatsapp: ''
  },
  education: {
    level: '',
    institution: ''
  },
  employment: {
    employmentStatus: 'Full-time',
    industry: '',
    jobTitle: '',
    primarySource: '',
    employer: '',
    occupation: '',
    tenure: '',
    primaryIncomeAmount: '',
    paysTax: false,
    yearsInWorkforce: '',
    otherSources: [],
    partnerSources: [],
    employmentBreaks: [],
    careerPlans: { plannedChange: '', potentialSalary: '', notes: '' },
    futureIncome: [],
    medicalMembers: 1, // Default to 1 (Primary)
    retirementContribution: ''
  },
  assets: [],
  liabilities: [],
  retirement: {
    retirementFunds: [],
    otherIncomeSources: [],
    activeAnnuities: []
  },
  goals: [],
  emergencyFund: {
    targetMonths: '',
    currentSavings: '',
    monthlyContribution: ''
  },
  relationshipStatus: { maritalStatus: 'Single', partnershipDate: '' },
  financialPlanning: {
    preferredInsurers: '',
    dislikedInsurers: '',
    plannedChanges: '',
    hasFinancialAdvisor: false,
    hasAccountant: false,
    accountantServices: { taxPlanning: false, investmentManagement: false },
    hasAttorney: false,
    attorneyServices: { estatePlanning: false }
  },
  expenses: {
    transportMode: '', fuelCost: '', autoInsurance: '', vehicleRepayment: '', hasMaintenancePlan: false, maintenanceCost: '', tollsAndTracker: '',
    livingSituation: '', timeAtAddress: '',
    groceries: '', diningOut: '',
    onFamilyPlan: false, medicalAidPlan: '', medicalAidPremium: '', openToMedicalOptions: false, gapCoverPlan: '', gapCoverPremium: '', openToGapOptions: false, otherMedicalCosts: '',
    childCare: '', childEducationAnnual: '', educationalExpenses: '',
    cleaningServices: '', landscaping: '', mortgagePayment: '', rentPayment: '', ratesAndTaxes: '', householdInsurance: '', otherHouseholdCosts: '',
    electricity: '', gas: '', water: '', internet: '', dstv: '', trashCollection: '',
    clothing: '', personalCare: '', petCare: '', hobbies: '', subscriptions: { gym: '', netflix: '', youtube: '', spotify: '', other: '' }, cellphoneData: '', travelBudget: '',
    giftsDonations: '', charitableDonations: '',
    creditCardPayments: '', personalLoans: '', studentLoans: '', otherDebt: '', childSupport: ''
  },
  lineItemNotes: {}
};

export default function DataEntryForm() {
  const [formData, setFormData] = useState<FormData>(initialFormData);

  const [isGenerating, setIsGenerating] = useState(false);
  const [clients, setClients] = useState<ClientFile[]>([]);
  const [selectedClient, setSelectedClient] = useState('');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState('');
  
  const formDataRef = useRef(formData);

  useEffect(() => {
    formDataRef.current = formData;
  }, [formData]);

  useEffect(() => {
    fetchClients();
  }, []);

  useEffect(() => {
    const intervalId = setInterval(() => {
      const currentData = formDataRef.current;
      if (currentData.clientName && currentData.clientName.trim() !== '') {
        saveDraft(currentData);
      }
    }, 10000);

    return () => clearInterval(intervalId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        });
      },
      { rootMargin: '-20% 0px -60% 0px' }
    );

    SECTIONS.forEach((section) => {
      const element = document.getElementById(section.id);
      if (element) observer.observe(element);
    });

    return () => observer.disconnect();
  }, []);

  const saveDraft = async (data: typeof formData) => {
    setSaveStatus('saving');
    try {
      const response = await fetch('/api/save-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });

      if (response.ok) {
        setSaveStatus('saved');
        setTimeout(() => setSaveStatus('idle'), 2000);
        fetchClients(true);
      } else {
        setSaveStatus('error');
      }
    } catch (error) {
      console.error('Autosave failed:', error);
      setSaveStatus('error');
    }
  };

  const handleNoteChange = (sectionId: string, fieldId: string, value: string) => {
    const key = `${sectionId}-${fieldId}`;
    setFormData(prev => ({
      ...prev,
      lineItemNotes: {
        ...prev.lineItemNotes,
        [key]: value
      }
    }));
  };

  const fetchClients = async (quiet = false) => {
    try {
      const response = await fetch('/api/clients');
      if (response.ok) {
        const data = await response.json();
        setClients(data);
      }
    } catch (error) {
      if (!quiet) console.error('Failed to fetch clients:', error);
    }
  };

  const loadClientData = async (filename: string) => {
    if (!filename) return;
    
    try {
      const response = await fetch(`/api/load-client?filename=${encodeURIComponent(filename)}`);
      if (response.ok) {
        const data = await response.json();
        // Ensure expenses object exists if loading old data
        const mergedData = {
            ...initialFormData,
            ...data,
            expenses: { ...initialFormData.expenses, ...data.expenses }
        };
        setFormData(mergedData);
        setSelectedClient(filename);
      }
    } catch (error) {
      console.error('Failed to load client data:', error);
      alert('Failed to load client data');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsGenerating(true);
    setErrorMessage(null);

    // Validate beneficiary percentages
    if (totalBeneficiaryPercentage > 100) {
      setErrorMessage(`Beneficiary percentages total ${totalBeneficiaryPercentage}%. This cannot exceed 100%. Please adjust before generating report.`);
      setIsGenerating(false);
      return;
    }

    try {
      const response = await fetch('/api/generate-pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });

      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${formData.clientName || 'document'}_report.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        fetchClients();
      } else {
        const errorData = await response.json().catch(() => ({}));
        setErrorMessage(errorData.message || 'Failed to generate PDF. Please try again.');
      }
    } catch (error) {
      console.error('Error:', error);
      setErrorMessage('An unexpected error occurred. Please check the console for details.');
    } finally {
      setIsGenerating(false);
    }
  };

  const addFamilyMember = () => {
    setFormData(prev => ({
      ...prev,
      familyMembers: [...prev.familyMembers, {
        title: '', firstName: '', surname: '', relationship: '', idNumber: '', 
        passportNumber: '', countryOfBirth: '', gender: '', dateOfBirth: '', 
        currentAge: '', ageNextBirthday: '', daysToNextBirthday: ''
      }]
    }));
  };

  const removeFamilyMember = (index: number) => {
    setFormData(prev => ({
      ...prev,
      familyMembers: prev.familyMembers.filter((_, i) => i !== index)
    }));
  };

  const totalBeneficiaryPercentage = formData.familyMembers.reduce((sum, member) => {
    return sum + (member.isBeneficiary ? (member.beneficiaryPercentage || 0) : 0);
  }, 0);

  // Calculate Monthly Gross Income for Expenses Section
  const calculateMonthlyGross = () => {
    const primary = parseFloat(formData.employment.primaryIncomeAmount) || 0;
    const other = formData.employment.otherSources.reduce((sum, item: IncomeSource) => sum + (parseFloat(item.amount) || 0), 0);
    const partner = formData.employment.partnerSources.reduce((sum, item: IncomeSource) => sum + (parseFloat(item.amount) || 0), 0);
    return primary + other + partner;
  };

  return (
    <div className="min-h-screen bg-gray-50/50 font-serif text-black">
      <div className="flex justify-center items-start gap-6 p-4 lg:p-8 max-w-[1600px] mx-auto">
        
        {/* Sidebar - Desktop Only */}
        <aside className="hidden xl:block w-64 sticky top-8 shrink-0 bg-white rounded-lg border border-gray-200 shadow-sm p-4 max-h-[calc(100vh-4rem)] overflow-y-auto">
          <SectionNavigation sections={SECTIONS} activeSection={activeSection} />
        </aside>

        {/* Main Content */}
        <main className="w-full max-w-6xl bg-white border border-gray-200 shadow-sm relative">
          {/* Utility Bar - Minimal */}
          <div className="flex justify-between items-center px-4 py-2 border-b border-gray-200 text-xs no-print sticky top-0 bg-white z-20">
            <div className="flex items-center gap-4">
              <span className="font-bold text-black uppercase tracking-wider">FNA System</span>
              <span className={`transition-opacity text-gray-400 ${saveStatus === 'saving' ? 'opacity-100' : 'opacity-0'}`}>Saving...</span>
              {saveStatus === 'error' && <span className="text-red-600">Save Failed</span>}
            </div>
            
            <div className="flex items-center gap-2">
              <button 
                type="button"
                onClick={() => window.print()}
                className="text-xs font-bold uppercase text-black hover:text-[#0000cc] tracking-wider px-3 border-r border-gray-200 transition-colors"
              >
                Print Form
              </button>
              <select 
                value={selectedClient}
                onChange={(e) => loadClientData(e.target.value)}
                className="bg-transparent border-b border-gray-200 px-2 py-0.5 focus:outline-none focus:border-blue-800 w-48 text-black text-xs font-serif uppercase"
              >
                <option value="">Load Client...</option>
                {clients.map((client) => (
                  <option key={client.filename} value={client.filename}>
                    {client.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Main Form */}
          <form onSubmit={handleSubmit} className="w-full">
        
        {/* Primary Member Section */}
        <div id="primary-member" className="scroll-mt-20">
          <MemberForm 
            isPrimary={true} 
            memberData={formData.primaryMember} 
            onChange={(data) => {
              // Auto-update client name when primary member name changes
              const fullName = `${data.firstName || ''} ${data.surname || ''}`.trim();
              setFormData(prev => ({
                ...prev, 
                primaryMember: data,
                clientName: fullName
              }));
            }}
          />
        </div>

        {/* Relationship Status */}
        <div id="relationship" className="scroll-mt-20">
          <RelationshipStatus 
            data={formData.relationshipStatus}
            onChange={(data) => setFormData(prev => ({...prev, relationshipStatus: data}))}
          />
        </div>

        {/* Family Members Section */}
        <div id="family" className="border-b border-gray-200 scroll-mt-20">
          <div className="px-3 py-2 border-b border-gray-200 flex justify-between items-center">
            <div className="flex items-center gap-4">
              <h3 className="text-xs font-bold uppercase text-black tracking-wider">Family Members</h3>
              {totalBeneficiaryPercentage > 100 && (
                <span className="text-[10px] font-bold text-red-600 animate-pulse">
                  ⚠ Beneficiary Total: {totalBeneficiaryPercentage}% (Exceeds 100%)
                </span>
              )}
            </div>
            <span className="text-[10px] text-gray-400 font-serif">{formData.familyMembers.length} record(s)</span>
          </div>
          
          {formData.familyMembers.map((member, index) => (
            <MemberForm 
              key={index}
              isPrimary={false} 
              memberData={member} 
              onChange={(data) => {
                setFormData(prev => {
                  const newMembers = [...prev.familyMembers];
                  newMembers[index] = data;
                  return {...prev, familyMembers: newMembers};
                });
              }}
              onRemove={() => removeFamilyMember(index)}
            />
          ))}

          <button
            type="button"
            onClick={addFamilyMember}
            className="w-full py-3 bg-transparent hover:text-blue-800 text-[#0000cc] text-[10px] font-bold uppercase tracking-wider transition-colors border-t border-gray-200"
          >
            + Add Family Member
          </button>
        </div>

        <div id="financial-planning" className="scroll-mt-20">
          <FinancialPlanningSection
            data={formData.financialPlanning}
            onChange={(data) => setFormData(prev => ({...prev, financialPlanning: data}))}
          />
        </div>

        {/* Contact Details */}
        <div id="contact" className="scroll-mt-20">
          <ContactDetails 
            data={formData.contactDetails}
            notes={formData.lineItemNotes || {}}
            onChange={(data) => setFormData(prev => ({...prev, contactDetails: data}))}
            onNoteChange={handleNoteChange}
          />
        </div>

        {/* Education Section */}
        <div id="education" className="scroll-mt-20">
          <EducationSection
            data={formData.education}
            onChange={(data) => setFormData(prev => ({...prev, education: data}))}
          />
        </div>

        {/* Employment & Income Section */}
        <div id="employment" className="scroll-mt-20">
          <EmploymentSection
            data={formData.employment}
            onChange={(data) => setFormData(prev => ({...prev, employment: data}))}
            age={Number(formData.primaryMember.currentAge) || 0}
            medicalMembers={formData.familyMembers.length + 1}
          />
        </div>

        {/* Expenses Section */}
        <div id="expenses" className="scroll-mt-20">
          <ExpensesSection
              data={formData.expenses}
              onChange={(data) => setFormData(prev => ({...prev, expenses: data}))}
              grossIncome={calculateMonthlyGross()}
          />
        </div>

        {/* Assets Section */}
        <div id="assets" className="scroll-mt-20">
          <AssetsSection
            assets={formData.assets || []}
            onChange={(assets: Asset[]) => setFormData(prev => ({...prev, assets}))}
          />
        </div>

        {/* Liabilities Section */}
        <div id="liabilities" className="scroll-mt-20">
          <LiabilitiesSection
            liabilities={formData.liabilities || []}
            assets={formData.assets || []}
            onChange={(liabilities) => setFormData(prev => ({...prev, liabilities}))}
          />
        </div>

        {/* Retirement Section */}
        <div id="retirement" className="scroll-mt-20">
          <RetirementSection
            data={formData.retirement}
            onChange={(data) => setFormData(prev => ({...prev, retirement: data}))}
            currentAge={Number(formData.primaryMember.currentAge) || 0}
            monthlySalary={parseFloat(formData.employment.primaryIncomeAmount) || 0}
            assets={formData.assets || []}
            liabilities={formData.liabilities || []}
          />
        </div>

        {/* Goals & Emergency Fund Section */}
        <div id="goals" className="scroll-mt-20">
          <GoalsSection
            goals={formData.goals || []}
            emergencyFund={formData.emergencyFund || { targetMonths: '', currentSavings: '', monthlyContribution: '' }}
            currentAge={Number(formData.primaryMember.currentAge) || 0}
            onGoalsChange={(goals: Goal[]) => setFormData(prev => ({...prev, goals}))}
            onEmergencyFundChange={(emergencyFund: EmergencyFund) => setFormData(prev => ({...prev, emergencyFund}))}
          />
        </div>

        {/* Action Bar */}
        <div className="p-4 border-t border-gray-200 flex flex-col items-end gap-2">
          {errorMessage && (
            <div className="text-red-600 text-xs font-bold uppercase tracking-wider">
              {errorMessage}
            </div>
          )}
          <button
            type="submit"
            disabled={isGenerating}
            className="bg-[#0000cc] hover:bg-blue-800 text-white h-10 px-8 text-xs font-bold uppercase tracking-wider transition-colors disabled:opacity-50"
          >
            {isGenerating ? 'Generating...' : 'Generate PDF Report'}
          </button>
        </div>
          </form>
        </main>
      </div>
    </div>
  );
}
