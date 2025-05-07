import streamlit as st
import re
import pandas as pd
import os
import json
from io import StringIO, BytesIO
import boto3
from botocore.exceptions import ClientError
import docx
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import time
import botocore.config

# Set page configuration
st.set_page_config(
    page_title="SQL Stored Procedure Analyzer",
    page_icon="🧰",
    layout="wide"
)

# Function to create a Word document from analysis
def create_word_document(analysis):
    # Create a new Document
    doc = Document()

    # Add title
    title = doc.add_heading('SQL Stored Procedure Analysis Report', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Add procedure name
    proc_name_heading = doc.add_heading('Stored Procedure Name:', level=1)
    # Make the procedure name itself bold in its own paragraph
    proc_name_para = doc.add_paragraph()
    proc_name_para.add_run(analysis['procedure_name']).bold = True

    # Add complexity
    doc.add_heading('Complexity:', level=1)
    doc.add_paragraph(analysis['complexity'])

    # Add scope
    doc.add_heading('Scope:', level=1)
    doc.add_paragraph(analysis['scope'])

    # Add optimization steps
    doc.add_heading('Optimization Steps:', level=1)

    for i, opt in enumerate(analysis["optimizations"], 1):
        # Step heading
        step_heading = doc.add_heading(f'Step {i}: {opt["type"]}', level=2)

        # Existing Logic
        doc.add_heading('Existing Logic:', level=3)
        existing_code = doc.add_paragraph(opt["existing_logic"])

        # Format code paragraph
        existing_code_fmt = existing_code.paragraph_format
        existing_code_fmt.left_indent = Inches(0.25)
        existing_code_fmt.right_indent = Inches(0.25)
        # Apply monospaced font to the runs within the paragraph
        for run in existing_code.runs:
            run.font.name = 'Courier New'
            # Optional: Set font size for code
            run.font.size = Pt(10)

        # Optimized Logic
        doc.add_heading('Optimized Logic:', level=3)
        optimized_code = doc.add_paragraph(opt["optimized_logic"])

        # Format code paragraph
        optimized_code_fmt = optimized_code.paragraph_format
        optimized_code_fmt.left_indent = Inches(0.25)
        optimized_code_fmt.right_indent = Inches(0.25)
        # Apply monospaced font to the runs within the paragraph
        for run in optimized_code.runs:
            run.font.name = 'Courier New'
            # Optional: Set font size for code
            run.font.size = Pt(10)

        # Explanation
        explanation_para = doc.add_paragraph()
        explanation_text = explanation_para.add_run(opt["explanation"])
        explanation_text.italic = True

        # Add separator paragraph
        separator = doc.add_paragraph()
        separator.add_run('_' * 40)

    # Add summary table
    doc.add_heading('Summary:', level=1)

    # Create table data (ensure line_number is handled if missing)
    table_data = []
    for opt in analysis["optimizations"]:
        table_data.append({
            "Type of Change": opt.get("type", "N/A"),
            "Line Number": opt.get("line_number", "N/A"), # Use .get for safety
            "Original Code Snippet": opt.get("existing_logic", ""),
            "Optimized Code Snippet": opt.get("optimized_logic", ""),
            "Optimization Explanation": opt.get("explanation", "")
        })

    # Add table to document only if there's data
    if table_data:
        num_rows = 1 + len(table_data)
        num_cols = 5
        table = doc.add_table(rows=num_rows, cols=num_cols)
        table.style = 'Table Grid' # Ensure 'Table Grid' style exists or use a known default

        # Set header row
        header_cells = table.rows[0].cells
        headers = ['Type of Change', 'Line Number', 'Original Code Snippet', 'Optimized Code Snippet', 'Optimization Explanation']
        for i, header_text in enumerate(headers):
            cell = header_cells[i]
            # Clear existing content (sometimes needed)
            cell.text = ''
            # Add text and format
            p = cell.paragraphs[0]
            run = p.add_run(header_text)
            run.bold = True
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER # Center align header text

        # Add data rows
        for i, item in enumerate(table_data):
            row_cells = table.rows[i+1].cells
            row_cells[0].text = item['Type of Change']
            row_cells[1].text = item['Line Number']
            row_cells[2].text = item['Original Code Snippet']
            row_cells[3].text = item['Optimized Code Snippet']
            row_cells[4].text = item['Optimization Explanation']

        # Set table column widths
        try:
            table.columns[0].width = Inches(1.2)
            table.columns[1].width = Inches(0.8) # Reduced width for line number
            table.columns[2].width = Inches(1.5)
            table.columns[3].width = Inches(1.5)
            table.columns[4].width = Inches(2.0) # Increased width for explanation
        except IndexError:
             st.warning("Could not set all table column widths.")

        # Apply alternate row shading
        for i, row in enumerate(table.rows):
            if i > 0 and i % 2 == 0:  # Even data rows (index 2, 4, ...) get shading
                for cell in row.cells:
                    tcPr = cell._tc.get_or_add_tcPr()
                    shading_elm = OxmlElement('w:shd')
                    shading_elm.set(qn('w:fill'), "F2F2F2") # Light gray color
                    shading_elm.set(qn('w:val'), 'clear') # Ensure fill type is set
                    shading_elm.set(qn('w:color'), 'auto')
                    tcPr.append(shading_elm)
    else:
        doc.add_paragraph("No optimization suggestions were generated.")

    # Save the document to a BytesIO object
    doc_io = BytesIO()
    doc.save(doc_io)
    doc_io.seek(0)

    return doc_io

# Function to analyze stored procedure using AWS Bedrock with Claude 3.5 Sonnet
def analyze_stored_procedure(file_content):
    try:
        # Initialize AWS Bedrock client with increased timeout and retry configuration
        boto_config = botocore.config.Config(
            connect_timeout=30,  # Increase connection timeout to 30 seconds
            read_timeout=120,    # Increase read timeout to 120 seconds
            retries={
                'max_attempts': 5,  # Maximum number of retry attempts
                'mode': 'standard'  # Use standard retry mode
            }
        )
        
        # Get AWS credentials from Streamlit secrets
        aws_access_key = st.secrets["aws"]["aws_access_key_id"]
        aws_secret_key = st.secrets["aws"]["aws_secret_access_key"]
        aws_region = st.secrets["aws"]["aws_region"]
        
        # Create boto3 client with credentials from Streamlit secrets
        bedrock = boto3.client(
            'bedrock-runtime',
            region_name=aws_region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            config=boto_config
        )
        
        model_id = "anthropic.claude-3-5-sonnet-20240620-v1:0"
        
        # Create prompt for analysis
        # Update the prompt in the analyze_stored_procedure function to better guide Claude to produce valid JSON
# Find the prompt definition in your code and replace it with this improved version:

        prompt = f"""
Analyze the following SQL stored procedure in its entirety and return your analysis in JSON format:

{file_content}

You are an expert in SQL performance optimization. Thoroughly analyze the stored procedure and identify the most significant optimization opportunities specific to this code. Focus on optimizations that would result in meaningful performance improvements.

Extract and provide:
1. The name of the stored procedure
2. The complexity level of the query that needs to be optimized
3. The scope/purpose of the stored procedure with details of 4-5 lines
4. Analyze the stored procedure line-by-line, identifying logical blocks and their performance implications. For each logical section, evaluate execution efficiency, resource usage, and potential bottlenecks. Provide specific optimization recommendations based on this detailed analysis.

For each optimization opportunity:
- Provide a clear, descriptive name for the type of optimization
- IMPORTANT: Indicate the EXACT line number range in the code where this optimization applies (e.g., "15-20", "32-45")
  Do NOT use generic terms like "Entire procedure" or "Throughout the procedure"
- Include the COMPLETE existing code snippet with context - NEVER use ellipses or abbreviations - show FULL code for the section
- Provide the COMPLETE improved code snippet with ALL necessary implementation details - NEVER use ellipses or abbreviations - show FULL code
- Explain the performance benefit and why this change would be impactful

EXTREMELY IMPORTANT CODE SNIPPET RULES:
1. ALWAYS provide COMPLETE code snippets showing the ENTIRE relevant section
2. NEVER abbreviate code with ellipses (...) or similar placeholders
3. Show the ENTIRE implementation for both existing and optimized code
4. If code is lengthy, still include the FULL code - do not summarize or truncate any part
5. Include ALL variable declarations, control structures, and statements in your code examples
6. For every optimization, ensure both original and optimized code snippets are COMPLETE with no parts missing

EXTREMELY IMPORTANT JSON RULES:
1. Your response must be VALID JSON that can be parsed with standard JSON parsers
2. All string values must be properly escaped - backslashes before quotes in strings (\\")
3. Do not include any control characters (\\n, \\r) in string values
4. Keep string values simple - avoid complex formatting
5. Always make sure all JSON strings are properly closed with double quotes
6. Every opening quote must have a closing quote - no unterminated strings
7. Do not include newlines within JSON string values - use space instead

Focus only on meaningful optimizations that would significantly improve performance or maintainability. Ignore minor stylistic issues like formatting, variable naming, or aliasing preferences.

Structure your response as valid JSON that matches this format exactly:
{
    "procedure_name": "name of the procedure",
    "complexity": "complexity level",
    "scope": "brief description",
    "optimizations": [
        {
            "type": "type of optimization",
            "line_number": "specific line number range (e.g., '15-20')",
            "existing_logic": "COMPLETE current code with NO abbreviations or ellipses",
            "optimized_logic": "COMPLETE improved code with NO abbreviations or ellipses",
            "explanation": "explanation of benefits"
        }
    ],
    "summary": {
        "original_performance_issues": "key issues overview",
        "optimization_impact": "estimated impact",
        "implementation_difficulty": "difficulty assessment"
    }
}

CRITICAL INSTRUCTIONS ABOUT CODE SNIPPETS:
1. Provide the COMPLETE code for each section you're addressing - NEVER truncate with ellipses
2. If showing a cursor, include ALL declarations, OPEN, FETCH, WHILE loop, CLOSE, and DEALLOCATE statements
3. If showing temp table creation, include ALL column definitions and constraints
4. Include ALL logic within control structures (IF/WHILE/etc.) - never abbreviate with "..."
5. For every code section, ensure 100% of the relevant code is displayed

Additionally use The following points involved to optimize the stored procedures and functions If found:
 
1) Identify the Index Usage and Removal of Unused Indices.
2) Removal of Redundant Usage of Distinct and Union (Alternate for this without any changes in the original logic).
3) Utilize set-based operations for inserts where possible, which is generally more efficient than row-by-row processing.
4) It's not necessary to use NOCOUNT on and off multiple times. Instead, set it on at the beginning and off at the end of the stored procedure.
5) Replace the nested loops with a Common Table Expression (CTE) for parsing the comma-separated values.
6) Instead of using * in the SELECT clause, explicitly list the columns needed. This can improve performance by fetching only the necessary columns and reducing data transfer.
7) Minimize the usage of dynamic sql. --- alternate for this code without any logic changes, only if the dynamic sql is used.
8) Usage of temporary tables (using temp tables in the code will be much efficient, for ex: by replacing cursors with temp tables).

FINAL INSTRUCTIONS:
1. Your response must contain ONLY valid JSON.
2. Do NOT include backticks or JSON code block markers.
3. DO NOT include newlines in JSON string values.
4. Keep all string values simple and properly escaped.
5. Check twice that every opening quote has a matching closing quote.
6. For each optimization, always provide specific line number ranges, never general locations.
7. NEVER use ellipses (...) or any other form of abbreviation in code examples.
8. Always show the COMPLETE code for each section being optimized.
       """
        # Generate response using Claude model with retry logic
        def generate_response(prompt):
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8000,  # Increased from default to 8000
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
                "system": "You are an expert SQL database optimizer. Your responses must be valid, properly escaped JSON. Do not use any special characters or line breaks in JSON string values that would cause parsing errors. Always complete all strings with proper closing quotes. Return ONLY the requested JSON format and nothing else."
            }
            
            # Initialize retry variables
            max_retries = 3
            retry_count = 0
            retry_delay = 5  # Initial delay of 5 seconds
            
            while retry_count <= max_retries:
                try:
                    response = bedrock.invoke_model(
                        modelId=model_id,
                        body=json.dumps(payload),
                        accept="application/json",
                        contentType="application/json"
                    )
                    
                    response_body = json.loads(response["body"].read())
                    return response_body["content"][0]["text"]
                    
                except (ClientError, Exception) as e:
                    retry_count += 1
                    error_message = str(e)
                    
                    # If we've exhausted all retries, give up and report the error
                    if retry_count > max_retries:
                        st.error(f"ERROR: Can't invoke model after {max_retries} attempts. Reason: {error_message}")
                        return None
                    
                    # Log the retry attempt
                    st.warning(f"Retry {retry_count}/{max_retries}: Error invoking model. Reason: {error_message}")
                    st.info(f"Waiting {retry_delay} seconds before retrying...")
                    
                    # Wait before retrying with exponential backoff
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
        
        # Get analysis results
        analysis_result = generate_response(prompt)
        
        if not analysis_result:
            return None
            
        # Debug: Display raw response for troubleshooting
        st.sidebar.expander("Debug Raw Response", expanded=False).code(analysis_result)
        
        # Clean the response: Remove any markdown formatting if present
        cleaned_response = analysis_result.strip()
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[7:]  # Remove ```json prefix
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3]  # Remove ``` suffix
            
        # Parse the JSON with additional error handling and repair attempts
        try:
            # First attempt: Try direct JSON parse
            return json.loads(cleaned_response)
        except json.JSONDecodeError as e:
            st.warning(f"Initial JSON parsing failed: {str(e)}")
            
            try:
                # Debug: Display problematic part of the response
                error_line = int(str(e).split("line")[1].split("column")[0].strip())
                start_line = max(0, error_line - 3)
                end_line = min(len(cleaned_response.split('\n')), error_line + 3)
                
                problematic_section = '\n'.join(cleaned_response.split('\n')[start_line:end_line])
                st.warning(f"Problematic section around line {error_line}:")
                st.code(problematic_section)
                
                # Basic fix attempt for common JSON errors
                # 1. Try to fix unterminated strings
                if "Unterminated string" in str(e):
                    st.info("Attempting to fix unterminated string...")
                    # Split by lines to find the problematic line
                    lines = cleaned_response.split('\n')
                    
                    # Loop through lines and try to fix unterminated strings
                    for i in range(len(lines)):
                        # If this is likely the problematic line (based on error message)
                        if i+1 == error_line:
                            # Add missing quote if needed
                            if lines[i].count('"') % 2 == 1:
                                lines[i] = lines[i] + '"'
                                st.info(f"Added closing quote to line {i+1}")
                    
                    repaired_json = '\n'.join(lines)
                    try:
                        return json.loads(repaired_json)
                    except json.JSONDecodeError:
                        st.warning("String repair attempt failed")
                
                # 2. Try using a more robust parsing library if available
                st.info("Attempting fallback approach with manual JSON extraction...")
                # Find the content between outermost { and }
                import re
                json_pattern = re.compile(r'\{.*\}', re.DOTALL)
                match = json_pattern.search(cleaned_response)
                if match:
                    extracted_json = match.group(0)
                    try:
                        return json.loads(extracted_json)
                    except json.JSONDecodeError:
                        st.warning("Regex extraction failed")
                
                # 3. If all else fails, try to extract and reconstruct the core data
                st.info("Attempting to reconstruct basic analysis structure...")
                # Extract procedure name
                proc_name_match = re.search(r'"procedure_name"\s*:\s*"([^"]+)"', cleaned_response)
                proc_name = proc_name_match.group(1) if proc_name_match else "Unknown"
                
                # Extract complexity
                complexity_match = re.search(r'"complexity"\s*:\s*"([^"]+)"', cleaned_response)
                complexity = complexity_match.group(1) if complexity_match else "Medium"
                
                # Extract scope
                scope_match = re.search(r'"scope"\s*:\s*"([^"]+)"', cleaned_response)
                scope = scope_match.group(1) if scope_match else "This stored procedure's scope could not be determined due to parsing issues."
                
                # Create a minimal functional JSON response
                minimal_json = {
                    "procedure_name": proc_name,
                    "complexity": complexity,
                    "scope": scope,
                    "optimizations": [
                        {
                            "type": "General Optimization",
                            "line_number": "N/A",
                            "existing_logic": "-- Original procedure code (could not be parsed)",
                            "optimized_logic": "-- See recommendations in the AI analysis text",
                            "explanation": "The JSON response from the AI service could not be fully parsed. Please review the raw response in the debug section."
                        }
                    ],
                    "summary": {
                        "original_performance_issues": "The JSON response could not be fully parsed.",
                        "optimization_impact": "See debug output for details.",
                        "implementation_difficulty": "N/A"
                    }
                }
                
                # Show error information
                st.error(f"Failed to parse JSON response: {str(e)}")
                st.code(cleaned_response)  # Show the problematic response
                
                # Return minimal functional structure
                return minimal_json
                
            except Exception as repair_error:
                st.error(f"JSON repair attempt failed: {str(repair_error)}")
                st.code(cleaned_response)  # Show the problematic response
                return None
    
    except Exception as e:
        st.error(f"Error during analysis: {str(e)}")
        import traceback
        st.sidebar.expander("Error Details", expanded=False).code(traceback.format_exc())
        return None
    
# UI Components 
st.title("SQL Stored Procedure Analyzer")
st.write("Upload a SQL stored procedure file for AI-powered optimization analysis")

st.markdown("---")
st.markdown("""
    ### About This Tool
    This app analyzes SQL stored procedures using AI to identify optimization opportunities.
    
    **Features:**
    - Extract procedure name and purpose
    - Identify optimization opportunities
    - Generate improved SQL code
    - Provide a summary of changes
    - Download formatted report as Word document
    """)

# Display a message about secrets configuration when in development
if not st.secrets.get("aws", {}).get("aws_access_key_id", ""):
    st.warning("""
    ⚠️ **AWS credentials not configured**
    
    To use this app, you need to configure AWS credentials in Streamlit secrets.
    
    1. Create a `.streamlit/secrets.toml` file locally with:
    ```toml
    [aws]
    aws_access_key_id = "YOUR_ACCESS_KEY"
    aws_secret_access_key = "YOUR_SECRET_KEY"
    aws_region = "us-east-1"
    ```
    
    2. In Streamlit Cloud, add these same secrets in the app settings.
    """)

# Sample SQL button for testing
if st.button("Load Sample SQL for Testing"):
    sample_sql = """
    CREATE PROCEDURE usp_GetCustomerOrders
    @CustomerId INT
    AS
    BEGIN
        SET NOCOUNT ON;
        
        -- Create temp table to store order data
        CREATE TABLE #TempOrders (
            OrderId INT,
            OrderDate DATETIME,
            OrderAmount DECIMAL(18,2)
        )
        
        -- Insert data into temp table
        INSERT INTO #TempOrders
        SELECT 
            OrderId,
            OrderDate,
            OrderAmount
        FROM Orders
        WHERE CustomerId = @CustomerId
        
        -- Cursor to process orders
        DECLARE @OrderId INT
        DECLARE @OrderDate DATETIME
        
        DECLARE order_cursor CURSOR FOR
        SELECT OrderId, OrderDate FROM #TempOrders
        
        OPEN order_cursor
        FETCH NEXT FROM order_cursor INTO @OrderId, @OrderDate
        
        WHILE @@FETCH_STATUS = 0
        BEGIN
            -- Update order status
            UPDATE Orders SET Status = 'Processed' WHERE OrderId = @OrderId
            UPDATE Orders SET LastModified = GETDATE() WHERE OrderId = @OrderId
            
            -- Process order details
            UPDATE OrderDetails 
            SET Processed = 1 
            WHERE OrderId = @OrderId
            
            FETCH NEXT FROM order_cursor INTO @OrderId, @OrderDate
        END
        
        CLOSE order_cursor
        DEALLOCATE order_cursor
        
        -- Return results
        SELECT 
            c.CustomerName,
            o.OrderId,
            o.OrderDate,
            o.OrderAmount,
            (SELECT COUNT(*) FROM OrderDetails WHERE OrderId = o.OrderId) AS ItemCount
        FROM 
            Customers c
            INNER JOIN Orders o ON c.CustomerId = o.CustomerId
        WHERE 
            c.CustomerId = @CustomerId
            
        -- Cleanup
        DROP TABLE #TempOrders
    END
    """
    st.session_state['sample_sql'] = sample_sql
    st.success("Sample SQL loaded! Click 'Analyze' to process it.")

# File upload component
uploaded_file = st.file_uploader("Upload SQL Stored Procedure", type=["sql"])

# Get SQL either from upload or sample
sql_content = None
if uploaded_file:
    sql_content = uploaded_file.getvalue().decode("utf-8")
elif 'sample_sql' in st.session_state:
    sql_content = st.session_state['sample_sql']
    st.info("Using sample SQL procedure. You can upload your own file to replace it.")

if sql_content:
    # Display the SQL
    with st.expander("View SQL Code", expanded=False):
        st.code(sql_content, language="sql")
    
    # Analysis button
    if st.button("Analyze SQL Procedure"):
        # Check if AWS credentials are configured before running analysis
        if not st.secrets.get("aws", {}).get("aws_access_key_id", ""):
            st.error("AWS credentials are not configured. Please set up Streamlit secrets first.")
        else:
            # Run analysis
            with st.spinner("Analyzing stored procedure... This may take up to 60 seconds."):
                analysis = analyze_stored_procedure(sql_content)
            
            if analysis:
                # Display results in tabs
                tab1, tab2 = st.tabs(["Analysis", "Download Report"])
                
                with tab1:
                    # Display the procedure name and scope
                    st.header(f"🔹 Stored Proc Name: `{analysis['procedure_name']}`")
                    
                    # Display the complexity
                    st.subheader("🔹 Complexity:")
                    st.write(analysis["complexity"])
                    
                    st.subheader("🔹 Scope:")
                    st.write(analysis["scope"])
                    
                    # Display optimization steps
                    st.subheader("🔹 Optimization Steps:")
                    
                    for i, opt in enumerate(analysis["optimizations"], 1):
                        st.markdown(f"⚙️ **Step {i}**: {opt['type']}")
                        
                        st.markdown("**Existing Logic:**")
                        st.code(opt["existing_logic"], language="sql")
                        
                        st.markdown("**Optimized Logic:**")
                        st.code(opt["optimized_logic"], language="sql")
                        
                        st.markdown(f"*{opt['explanation']}*")
                        st.markdown("---")
                    
                    # Create and display summary table
                    st.subheader("🔹 Summary:")
                    
                    summary_data = []
                    for opt in analysis["optimizations"]:
                        summary_data.append({
                            "Type of Change": opt["type"],
                            "Line Number": opt["line_number"],
                            "Original Code Snippet": opt["existing_logic"],
                            "Optimized Code Snippet": opt["optimized_logic"],
                            "Optimization Explanation": opt["explanation"]
                        })
                    
                    summary_df = pd.DataFrame(summary_data)
                    
                    # Display as a formatted table with custom styling
                    st.markdown("""
                    <style>
                    .summary-table {
                        font-size: 0.85rem;
                        border-collapse: collapse;
                        width: 100%;
                    }
                    .summary-table th {
                        background-color: #f2f2f2;
                        text-align: left;
                        padding: 8px;
                        border: 1px solid #ddd;
                    }
                    .summary-table td {
                        text-align: left;
                        padding: 8px;
                        border: 1px solid #ddd;
                    }
                    .summary-table tr:nth-child(even) {
                        background-color: #f9f9f9;
                    }
                    </style>
                    """, unsafe_allow_html=True)
                    
                    # Convert dataframe to HTML table with custom classes
                    table_html = summary_df.to_html(classes='summary-table', escape=False, index=False)
                    st.markdown(table_html, unsafe_allow_html=True)
                    
                    # Display additional summary information
                    if "summary" in analysis:
                        st.subheader("🔹 Overall Assessment:")
                        if "original_performance_issues" in analysis["summary"]:
                            st.markdown(f"**Original Issues:** {analysis['summary']['original_performance_issues']}")
                        if "optimization_impact" in analysis["summary"]:
                            st.markdown(f"**Impact of Optimizations:** {analysis['summary']['optimization_impact']}")
                        if "implementation_difficulty" in analysis["summary"]:
                            st.markdown(f"**Implementation Difficulty:** {analysis['summary']['implementation_difficulty']}")
                
                with tab2:
                    # Create Word document
                    with st.spinner("Generating Word document..."):
                        docx_bytes = create_word_document(analysis)
                    
                    # Provide download button for DOCX
                    st.download_button(
                        label="⬇️ Download Report as Word Document",
                        data=docx_bytes,
                        file_name=f"{analysis['procedure_name']}_analysis.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key='docx-download'
                    )
                    
                    # Also provide markdown option
                    report_md = f"""# SQL Stored Procedure Analysis Report

## Procedure Name: {analysis['procedure_name']}

## Complexity:
{analysis['complexity']}

## Scope:
{analysis['scope']}

## Optimization Steps:
"""
                    
                    for i, opt in enumerate(analysis["optimizations"], 1):
                        report_md += f"""
### Step {i}: {opt['type']}

**Existing Logic:**
```sql
{opt['existing_logic']}
```

**Optimized Logic:**
```sql
{opt['optimized_logic']}
```

*{opt['explanation']}*

---
"""
                    
                    report_md += "\n## Summary Table:\n\n"
                    report_md += summary_df.to_markdown(index=False)
                    
                    st.download_button(
                        label="⬇️ Download Report as Markdown",
                        data=report_md,
                        file_name=f"{analysis['procedure_name']}_analysis.md",
                        mime="text/markdown",
                        key='md-download'
                    )
                    
                    st.info("The Word document (.docx) contains the same content as shown in the 'Analysis' tab, but in a properly formatted document for sharing.")
            else:
                st.error("Analysis could not be completed. Please check the Debug section in the sidebar for more details.")

else:
    # Show example when no file is uploaded
    st.info("Please upload a SQL stored procedure file (.sql) or use the sample SQL to begin analysis.")
    
    with st.expander("See Example Analysis"):
        st.markdown("""
        ## Example Output
        
        🔹 **Stored Proc Name:** `usp_get_customer_data`
        
        🔹 **Complexity:** Medium
        
        🔹 **Scope:**  
        This procedure retrieves customer data including their purchase history, last login time, and calculates their loyalty score using internal metrics.
        
        🔹 **Optimization Steps:**
        
        ⚙️ **Step 1:** Replace Multiple Updates
        
        **Existing Logic:**
        ```sql
        UPDATE table SET col1 = val WHERE condition;
        UPDATE table SET col2 = val WHERE condition;
        ```
        
        **Optimized Logic:**
        ```sql
        UPDATE table 
        SET col1 = val, 
            col2 = val 
        WHERE condition;
        ```
        
        *Reduces write operations and improves efficiency.*
        """)
        
        # Example of the summary table
        st.markdown("🔹 **Summary:**")
        example_data = [{
            "Type of Change": "Replace Multiple Updates",
            "Line Number": "Identified in multiple places",
            "Original Code Snippet": "UPDATE table SET col1 = val WHERE condition;\nUPDATE table SET col2 = val WHERE condition;",
            "Optimized Code Snippet": "UPDATE table \nSET col1 = val, \n    col2 = val \nWHERE condition;",
            "Optimization Explanation": "Reduces write operations and improves efficiency."
        }, {
            "Type of Change": "Index on Temp Tables",
            "Line Number": "Where temp tables are created",
            "Original Code Snippet": "CREATE TABLE #temp (\n    id INT,\n    value VARCHAR(50)\n)",
            "Optimized Code Snippet": "CREATE TABLE #temp (\n    id INT,\n    value VARCHAR(50)\n);\nCREATE INDEX IX_temp_id ON #temp(id);",
            "Optimization Explanation": "Improves performance by speeding up lookups and joins."
        }]

        example_df = pd.DataFrame(example_data)

        # Display example table with styling
        st.markdown("""
        <style>
        .summary-table {
            font-size: 0.85rem;
            border-collapse: collapse;
            width: 100%;
        }
        .summary-table th {
            background-color: #f2f2f2;
            text-align: left;
            padding: 8px;
            border: 1px solid #ddd;
        }
        .summary-table td {
            text-align: left;
            padding: 8px;
            border: 1px solid #ddd;
        }
        .summary-table tr:nth-child(even) {
            background-color: #f9f9f9;
        }
        </style>
        """, unsafe_allow_html=True)
        
        table_html = example_df.to_html(classes='summary-table', escape=False, index=False)
        st.markdown(table_html, unsafe_allow_html=True)

# Add footer
st.markdown("---")
st.caption("SQL Stored Procedure Analyzer powered by AWS Bedrock Claude 3.5 Sonnet")