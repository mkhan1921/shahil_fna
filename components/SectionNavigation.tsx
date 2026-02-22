import React from 'react';

interface Section {
  id: string;
  label: string;
}

interface SectionNavigationProps {
  sections: Section[];
  activeSection: string;
}

const SectionNavigation: React.FC<SectionNavigationProps> = ({ sections, activeSection }) => {
  return (
    <nav className="w-full">
      <h4 className="text-xs font-bold uppercase text-gray-500 mb-3 px-2">Sections</h4>
      <ul className="space-y-1">
        {sections.map((section) => (
          <li key={section.id}>
            <a
              href={`#${section.id}`}
              className={`block text-xs py-1.5 transition-colors rounded-md ${
                activeSection === section.id
                  ? 'bg-blue-50 text-blue-800 font-bold px-3'
                  : 'text-gray-600 hover:text-black hover:bg-gray-100 px-3'
              }`}
              onClick={(e) => {
                e.preventDefault();
                document.getElementById(section.id)?.scrollIntoView({ behavior: 'smooth' });
              }}
            >
              {section.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
};

export default SectionNavigation;
