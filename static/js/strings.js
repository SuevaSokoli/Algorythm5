// All user-facing text lives here so the UI can be translated (CLAUDE.md D4).

export const STRINGS = {
  appName: "Algorythm5",
  tagline: "Clean customer lists, ready for your newsletter",
  steps: ["Upload", "Check columns", "Cleaning", "Results"],
  theme: { label: "Color theme", light: "Light", dark: "Dark", system: "Auto" },

  upload: {
    title: "Upload your customer files",
    lead: "Drop one file or several exports at once; we combine them into one clean list. We fix names, phones and emails, merge duplicates and remove people who opted out. Your files never leave this computer.",
    drop: "Drop your files here",
    or: "or",
    choose: "Choose files",
    hint: "CSV or Excel (.xlsx), up to 10 files of 10 MB each. Column order and headers don't need to match.",
    reading: (count) => (count === 1 ? "Reading your file…" : `Reading ${count} files…`),
    wrongType: (name) => `${name}: please choose .csv or .xlsx files.`,
    tooLarge: (name) => `${name} is larger than 10 MB. Please split it or remove unused columns.`,
    tooMany: (max) => `Please choose at most ${max} files at once.`,
  },

  mapping: {
    title: "Check the columns",
    lead: "We matched the columns of each file by their headers and their values. Please check them; change any that look wrong.",
    fileInfo: (file) =>
      [`${file.rows} ${file.rows === 1 ? "row" : "rows"}`, file.file_kind.toUpperCase(), file.encoding, file.delimiter ? `separator "${file.delimiter}"` : null, file.sheet_name ? `sheet ${file.sheet_name}` : null]
        .filter(Boolean)
        .join(" · "),
    noHeader: "This file has no header row. We recognized the columns from their values; please check them.",
    repeated: (original) => `This file is the same as ${original}. It will be ignored so nobody is counted twice.`,
    reasons: {
      numbered: "Matched by header",
      fuzzy: "Header looks similar",
      content: "Recognized from the values",
      header_mismatch: "Header didn't fit the values; matched by values",
      merged_names: "Holds full names; will be split",
      duplicate_column: "Field already used by another column",
    },
    column: "Your column",
    examples: "Examples",
    becomes: "Becomes",
    keepExtra: "Keep as extra column",
    confidence: { 1: "Sure", 0.8: "Likely", 0.6: "Guess", manual: "Your choice", none: "Not matched" },
    preview: "Preview of the first rows",
    back: "Choose other files",
    submit: (count) => (count === 1 ? "Clean my file" : `Clean and combine ${count} files`),
    needContact: "Please choose at least one Email or Phone column.",
    duplicate: (label) => `"${label}" is chosen twice. Each of these can only be used once.`,
    empty: "(empty)",
  },

  progress: {
    title: "Cleaning your file",
    working: "Fixing names, phones and emails, and looking for duplicates…",
    slow: "This is taking longer than usual. You can keep waiting or try again later.",
    failed: "We couldn't clean this file.",
    tryAgain: "Try another file",
  },

  results: {
    title: "Your clean file is ready",
    lead: "Download the clean file for your newsletter. Rows we weren't sure about are in the review list.",
    cards: {
      rows_in: "Rows received",
      ready: "Ready for newsletter",
      fixed: "Fixed automatically",
      review: "Need your review",
      merged: "Duplicates merged",
      opted_out: "Opted out",
      rejected: "Couldn't use",
    },
    downloadMain: "Download clean file (.xlsx)",
    downloads: { clean_csv: "Clean file (.csv)", changes_csv: "Change log (.csv)", review_csv: "Review list (.csv)" },
    tabs: { review: "Review list", changes: "All changes" },
    filterProblem: "Problem type",
    filterRule: "Rule",
    filterField: "Field",
    all: "All",
    noReview: "Nothing needs your review.",
    noChanges: "No changes were needed.",
    columns: {
      record: "Row",
      field: "Field",
      problem: "Problem",
      value: "Value",
      suggestion: "Suggestion",
      before: "Before",
      after: "After",
      why: "Why",
    },
    page: (page, pages) => `Page ${page} of ${pages}`,
    previous: "Previous",
    next: "Next",
    sourceRows: (rows) => `Row ${rows}`,
    jobLevel: "Whole file",
    deleteJob: "Delete this job now",
    deleteTitle: "Delete this job?",
    deleteBody: "The uploaded file, the results and all downloads will be removed from this computer. This can't be undone.",
    deleteConfirm: "Delete",
    cancel: "Cancel",
    deleted: "The job was deleted.",
    another: "Clean other files",
    files: {
      title: "Per file",
      columns: { file: "File", rows: "Rows", ready: "Ready", review: "Review", merged: "Merged", opted_out: "Opted out", rejected: "Couldn't use" },
      ignored: (original) => `Ignored: same as ${original}`,
      mergeNote: "Duplicates across files are listed in the Merges and \"Same name, different people\" sheets of the Excel report.",
    },
    severity: { warning: "Check", blocking: "Excluded" },
    empty: "(empty)",
  },

  errors: {
    network: "We couldn't reach Algorythm5. Is it still running?",
    generic: "Something went wrong. Please try again.",
  },

  fields: {
    customer_id: "Customer ID",
    salutation: "Title (Mr., Znj., …)",
    first_name: "First name",
    last_name: "Last name",
    full_name: "Full name",
    email: "Email",
    phone: "Phone",
    mobile: "Mobile phone",
    phone_extension: "Phone extension",
    company: "Company",
    street: "Street / address",
    postal_code: "Postal code",
    city: "City",
    country: "Country",
    birth_date: "Birth date",
    signup_date: "Sign-up date",
    opt_out: "Opt-out / unsubscribed",
    consent: "Newsletter consent",
    notes: "Notes",
  },
};

export const MULTI_SOURCE_FIELDS = new Set(["email", "phone", "mobile"]);
export const CONTACT_FIELDS = new Set(["email", "phone", "mobile"]);
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_FILES = 10;
export const ACCEPTED_EXTENSIONS = [".csv", ".xlsx"];
