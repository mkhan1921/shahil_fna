import React, { useState } from 'react';

interface NoteButtonProps {
  sectionId: string;
  fieldId: string;
  notes: Record<string, string>;
  onNoteChange: (sectionId: string, fieldId: string, value: string) => void;
}

const NoteButton: React.FC<NoteButtonProps> = ({ sectionId, fieldId, notes, onNoteChange }) => {
  const noteKey = `${sectionId}-${fieldId}`;
  const hasNote = Boolean(notes[noteKey] && notes[noteKey].trim().length > 0);
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative flex items-center h-full">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`w-6 h-6 flex items-center justify-center transition-colors hover:bg-gray-100 rounded-full ${hasNote ? 'text-blue-600' : 'text-gray-300 hover:text-gray-500'}`}
        title="Add/View Note"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 20h9"></path>
          <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-8 top-0 z-50 w-64 bg-white border border-gray-200 shadow-lg rounded-md p-2 animate-in fade-in zoom-in duration-200 origin-top-right">
            <div className="flex justify-between items-center mb-1">
                <span className="text-[10px] font-bold uppercase text-gray-500">Note</span>
                <button 
                    type="button" 
                    onClick={() => setIsOpen(false)} 
                    className="text-gray-400 hover:text-black"
                >
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
          <textarea
            value={notes[noteKey] || ''}
            onChange={(e) => onNoteChange(sectionId, fieldId, e.target.value)}
            className="w-full text-xs border border-gray-200 rounded p-1.5 focus:outline-none focus:border-blue-500 min-h-[80px] font-serif"
            placeholder="Add a note..."
            autoFocus
          />
        </div>
      )}
    </div>
  );
};

export default NoteButton;
