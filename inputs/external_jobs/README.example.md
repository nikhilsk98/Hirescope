# External jobs drop folder

Put a job description here (.txt, .md, .pdf or .docx) and the next HireScope run imports it as an "External" job
(or run `./scripts/add_job.sh --pending` to import now). Imported files move to `data.nosync/external_jobs/`.

Say which company and role it is in either of two ways:

1. A small header at the top of a text file, then a blank line:

       Company: Acme Robotics
       Role: Software Engineer Co-op
       URL: https://acme.example/careers/123
       Location: Boston, MA
       Track: coop

       <the job description…>

2. The file name: `Acme Robotics - Software Engineer Co-op.pdf` (or `Acme__Software_Engineer.docx`).

Files that can't be imported stay here; the reason is in `data.nosync/logs/jobsearch.log` and the run summary.
