/**
 * Web拆票应用 - UI处理逻辑
 */

let currentResult = null;
let currentHeaders = null;

// ========================
// 文件上传处理
// ========================
function initFileUpload() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');

    // 点击上传
    dropZone.addEventListener('click', () => fileInput.click());

    // 文件选择
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) handleFile(e.target.files[0]);
    });

    // 拖拽
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) handleFile(e.dataTransfer.files[0]);
    });
}

// ========================
// 处理上传的Excel文件
// ========================
async function handleFile(file) {
    const fileName = file.name;
    const fileExt = fileName.split('.').pop().toLowerCase();

    if (!['xlsx', 'xls', 'xlsm'].includes(fileExt)) {
        showAlert('请上传 Excel 文件（.xlsx / .xls / .xlsm）', 'error');
        return;
    }

    showProcessing(true);
    updateProgress('正在读取文件...');

    try {
        const arrayBuffer = await file.arrayBuffer();
        const workbook = XLSX.read(arrayBuffer, { type: 'array', cellDates: true });

        updateProgress('正在解析数据...');

        // 读取数据sheet
        const sheetName = SPLIT_CONFIG.sourceSheet;
        if (!workbook.SheetNames.includes(sheetName)) {
            showAlert(`未找到 "${sheetName}" sheet，请确认文件格式正确`, 'error');
            showProcessing(false);
            return;
        }

        const ws = workbook.Sheets[sheetName];
        const rows = XLSX.utils.sheet_to_json(ws, { defval: '' });

        if (rows.length === 0) {
            showAlert('数据为空，请检查文件内容', 'error');
            showProcessing(false);
            return;
        }

        // 读取豁免清单
        let exemptionCodes = new Set(SPLIT_CONFIG.defaultExemptionCodes);
        if (workbook.SheetNames.includes(SPLIT_CONFIG.exemptionSheet)) {
            const wsEx = workbook.Sheets[SPLIT_CONFIG.exemptionSheet];
            const exRows = XLSX.utils.sheet_to_json(wsEx, { defval: '' });
            for (const row of exRows) {
                const code = cleanHSCodeForLoad(row['豁免税号'] || row[Object.keys(row)[0]] || '');
                if (code.length === 8) exemptionCodes.add(code);
            }
        }

        updateProgress(`正在执行拆票逻辑（${rows.length} 行数据）...`);

        // 执行拆票
        const engine = new SplitEngine(SPLIT_CONFIG);
        const result = engine.process(rows, exemptionCodes);

        // uodId数据完整性校验
        updateProgress('正在执行uodId数据完整性校验...');
        const validationReport = engine.validateByUodId(rows, result.resultRows);
        result.validation = validationReport;

        currentResult = result;
        currentHeaders = Object.keys(rows[0]);

        updateProgress('正在生成预览...');

        // 显示结果
        displayResults(result, rows);

        // 显示下载按钮
        document.getElementById('downloadSection').style.display = 'block';
        document.getElementById('uploadInfo').textContent =
            `${fileName} → ${rows.length} 行 → ${Object.keys(result.ticketNumbers).length} 票`;

        showProcessing(false);

    } catch (err) {
        console.error(err);
        showAlert('处理文件时出错：' + err.message, 'error');
        showProcessing(false);
    }
}

// ========================
// HSCODE清洗（用于加载豁免清单）
// ========================
function cleanHSCodeForLoad(hsCode) {
    if (!hsCode) return '';
    hsCode = String(hsCode);
    let result = '';
    for (let i = 0; i < hsCode.length; i++) {
        const c = hsCode[i];
        if (c >= '0' && c <= '9') result += c;
    }
    if (result.length > 8) result = result.substring(0, 8);
    if (result.length < 8 && result.length > 0) {
        result = '0'.repeat(8 - result.length) + result;
    }
    return result;
}

// ========================
// 显示结果
// ========================
function displayResults(result, originalRows) {
    // 统计信息
    const stats = document.getElementById('stats');
    const ticketCount = Object.keys(result.ticketNumbers).length;
    const rowCount = result.resultRows.filter(r => r !== null).length;
    const inconsistentCount = result.inconsistentInfo.highlightedRows.size;
    const validationPassed = result.validation ? result.validation.passed : false;

    stats.innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${rowCount}</div>
            <div class="stat-label">数据行</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${ticketCount}</div>
            <div class="stat-label">分票数</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${inconsistentCount}</div>
            <div class="stat-label">不一致行</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="color: ${validationPassed ? '#0F6E56' : '#A32D2D'};">${validationPassed ? '\u2705' : '\u274c'}</div>
            <div class="stat-label">uodId校验</div>
        </div>
    `;

    // 预览表格
    displayTable(result);

    // 拆分理由
    displayReasons(result.reasons);

    // 数据校验
    displayValidation(result.validation);
}

// ========================
// 显示预览表格
// ========================
function displayTable(result) {
    const container = document.getElementById('resultTable');
    const displayColumns = [
        'uodId', '分票编号', '分票', '产品编号', '产品名称',
        '大PO', '小PO', '备案单位', '原产国',
        '账册号', '业务申报表号', '客户指令号'
    ];

    // 限制预览行数
    const maxPreviewRows = 200;
    const rows = result.resultRows;
    const previewRows = rows.length > maxPreviewRows ? rows.slice(0, maxPreviewRows) : rows;

    let html = '<table class="result-table"><thead><tr>';
    for (const col of displayColumns) {
        html += `<th>${col}</th>`;
    }
    html += '</tr></thead><tbody>';

    for (const row of previewRows) {
        if (row === null) {
            html += '<tr class="blank-row"><td colspan="' + displayColumns.length + '"></td></tr>';
            continue;
        }

        const isHighlighted = row._isInconsistent || false;
        const rowClass = isHighlighted ? 'highlighted-row' : '';

        html += `<tr class="${rowClass}">`;
        for (const col of displayColumns) {
            const value = row[col] != null ? String(row[col]) : '';
            const isRed = isHighlighted && row._redColumns && row._redColumns.has(col);
            const cellClass = isRed ? 'red-cell' : '';
            html += `<td class="${cellClass}">${escapeHtml(value)}</td>`;
        }
        html += '</tr>';
    }

    html += '</tbody></table>';

    if (rows.length > maxPreviewRows) {
        html += `<p class="preview-note">仅显示前 ${maxPreviewRows} 行，完整结果请下载Excel文件</p>`;
    }

    // 图例
    html += `
        <div class="legend">
            <span class="legend-item"><span class="legend-color yellow-bg"></span>黄色行：同票内同产品编号，大PO/小PO/备案单位/原产国任一不一致</span>
            <span class="legend-item"><span class="legend-color red-font"></span>红色字体：具体不一致的单元格</span>
        </div>
    `;

    container.innerHTML = html;
}

// ========================
// 显示拆分理由
// ========================
function displayReasons(reasons) {
    const container = document.getElementById('reasonsTable');

    if (!reasons || reasons.length === 0) {
        container.innerHTML = '<p class="no-data">无拆分理由</p>';
        return;
    }

    let html = '<table class="reasons-table"><thead><tr>';
    html += '<th>分票编号</th><th>对应分票标记</th><th>拆分理由</th>';
    html += '</tr></thead><tbody>';

    for (const r of reasons) {
        html += '<tr>';
        html += `<td class="ticket-no">${escapeHtml(r.ticketNo)}</td>`;
        html += `<td class="mark-text">${escapeHtml(r.mark)}</td>`;
        html += `<td class="reason-text"><pre>${escapeHtml(r.reason)}</pre></td>`;
        html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

// ========================
// 显示数据校验结果
// ========================
function displayValidation(report) {
    const container = document.getElementById('validationTable');
    if (!report) {
        container.innerHTML = '<p class="no-data">校验未执行</p>';
        return;
    }

    let html = '';

    // 总体结论
    const statusClass = report.passed ? 'validation-pass' : 'validation-fail';
    const statusIcon = report.passed ? '\u2705' : '\u274c';
    const statusText = report.passed
        ? '数据完整性校验通过：无行丢失、无行多余、无重复、无字段错位'
        : `数据完整性校验未通过：发现 ${report.totalMismatches} 个问题`;

    html += `<div class="validation-summary ${statusClass}">
        <span class="validation-icon">${statusIcon}</span>
        <span class="validation-text">${statusText}</span>
    </div>`;

    // 检查项明细
    html += '<table class="validation-table"><tbody>';
    html += `<tr><td class="validation-label">原始数据行数</td><td>${report.originalCount}</td></tr>`;
    html += `<tr><td class="validation-label">结果数据行数</td><td>${report.resultCount}</td></tr>`;
    html += `<tr><td class="validation-label">行数匹配</td><td>${report.countMatch ? '\u2705 通过' : '\u274c 不一致'}</td></tr>`;
    html += `<tr><td class="validation-label">丢失的uodId</td><td>${report.missingIds.length === 0 ? '\u2705 无' : '\u274c ' + report.missingIds.length + ' 个'}</td></tr>`;
    html += `<tr><td class="validation-label">多余的uodId</td><td>${report.extraIds.length === 0 ? '\u2705 无' : '\u274c ' + report.extraIds.length + ' 个'}</td></tr>`;
    html += `<tr><td class="validation-label">重复的uodId</td><td>${report.duplicateIds.length === 0 ? '\u2705 无' : '\u274c ' + report.duplicateIds.length + ' 个'}</td></tr>`;
    html += `<tr><td class="validation-label">字段错位</td><td>${report.fieldMismatches.length === 0 ? '\u2705 无' : '\u274c ' + report.fieldMismatches.length + ' 处'}</td></tr>`;
    html += '</tbody></table>';

    // 丢失的uodId明细
    if (report.missingIds.length > 0) {
        html += '<h4 class="validation-detail-title">丢失的uodId明细</h4>';
        html += '<table class="validation-table"><thead><tr><th>#</th><th>uodId</th></tr></thead><tbody>';
        for (let i = 0; i < Math.min(report.missingIds.length, 50); i++) {
            html += `<tr><td>${i + 1}</td><td>${escapeHtml(report.missingIds[i])}</td></tr>`;
        }
        html += '</tbody></table>';
        if (report.missingIds.length > 50) {
            html += `<p class="preview-note">仅显示前50条，共 ${report.missingIds.length} 条</p>`;
        }
    }

    // 多余的uodId明细
    if (report.extraIds.length > 0) {
        html += '<h4 class="validation-detail-title">多余的uodId明细</h4>';
        html += '<table class="validation-table"><thead><tr><th>#</th><th>uodId</th></tr></thead><tbody>';
        for (let i = 0; i < Math.min(report.extraIds.length, 50); i++) {
            html += `<tr><td>${i + 1}</td><td>${escapeHtml(report.extraIds[i])}</td></tr>`;
        }
        html += '</tbody></table>';
        if (report.extraIds.length > 50) {
            html += `<p class="preview-note">仅显示前50条，共 ${report.extraIds.length} 条</p>`;
        }
    }

    // 重复的uodId明细
    if (report.duplicateIds.length > 0) {
        html += '<h4 class="validation-detail-title">重复的uodId明细</h4>';
        html += '<table class="validation-table"><thead><tr><th>uodId</th><th>出现次数</th></tr></thead><tbody>';
        for (const d of report.duplicateIds) {
            html += `<tr><td>${escapeHtml(d.uodId)}</td><td>${d.count}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    // 字段错位明细
    if (report.fieldMismatches.length > 0) {
        html += '<h4 class="validation-detail-title">字段错位明细（前50条）</h4>';
        html += '<table class="validation-table"><thead><tr><th>uodId</th><th>字段</th><th>原始值</th><th>结果值</th></tr></thead><tbody>';
        for (let i = 0; i < Math.min(report.fieldMismatches.length, 50); i++) {
            const m = report.fieldMismatches[i];
            html += `<tr><td>${escapeHtml(m.uodId)}</td><td>${escapeHtml(m.field)}</td><td>${escapeHtml(m.originalValue)}</td><td>${escapeHtml(m.resultValue)}</td></tr>`;
        }
        html += '</tbody></table>';
        if (report.fieldMismatches.length > 50) {
            html += `<p class="preview-note">仅显示前50条，共 ${report.fieldMismatches.length} 条</p>`;
        }
    }

    container.innerHTML = html;
}

// ========================
// 下载结果
// ========================
function downloadResult() {
    if (!currentResult) {
        showAlert('请先上传文件并完成拆票', 'error');
        return;
    }

    const result = currentResult;
    const headers = currentHeaders;

    // 构建输出数据（数组形式）
    const outputHeaders = ['分票编号', '分票', ...headers];
    const aoa = [outputHeaders];

    for (const row of result.resultRows) {
        if (row === null) {
            // 空行
            const emptyRow = new Array(outputHeaders.length).fill('');
            aoa.push(emptyRow);
        } else {
            const rowData = [
                row['分票编号'] || '',
                row['分票'] || ''
            ];
            for (const h of headers) {
                rowData.push(row[h] != null ? row[h] : '');
            }
            aoa.push(rowData);
        }
    }

    // 创建工作簿
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = [{ wch: 14 }, { wch: 40 }];
    XLSX.utils.book_append_sheet(wb, ws, '中芯');

    // 添加分票理由sheet
    const reasonHeaders = ['分票编号', '对应分票标记', '拆分理由'];
    const reasonAoa = [reasonHeaders];
    for (const r of result.reasons) {
        reasonAoa.push([r.ticketNo, r.mark, r.reason]);
    }
    const wsReason = XLSX.utils.aoa_to_sheet(reasonAoa);
    wsReason['!cols'] = [{ wch: 14 }, { wch: 50 }, { wch: 80 }];
    XLSX.utils.book_append_sheet(wb, wsReason, '分票理由');

    // 添加数据校验sheet
    const vReport = result.validation;
    if (vReport) {
        const vAoa = [
            ['数据完整性校验报告'],
            [],
            ['校验结果', vReport.passed ? '通过' : '未通过'],
            ['原始数据行数', vReport.originalCount],
            ['结果数据行数', vReport.resultCount],
            ['行数匹配', vReport.countMatch ? '是' : '否'],
            ['丢失uodId数', vReport.missingIds.length],
            ['多余uodId数', vReport.extraIds.length],
            ['重复uodId数', vReport.duplicateIds.length],
            ['字段错位数', vReport.fieldMismatches.length],
            ['总问题数', vReport.totalMismatches],
            []
        ];

        if (vReport.missingIds.length > 0) {
            vAoa.push(['=== 丢失的uodId ===']);
            vAoa.push(['uodId']);
            for (const id of vReport.missingIds) {
                vAoa.push([id]);
            }
            vAoa.push([]);
        }

        if (vReport.extraIds.length > 0) {
            vAoa.push(['=== 多余的uodId ===']);
            vAoa.push(['uodId']);
            for (const id of vReport.extraIds) {
                vAoa.push([id]);
            }
            vAoa.push([]);
        }

        if (vReport.duplicateIds.length > 0) {
            vAoa.push(['=== 重复的uodId ===']);
            vAoa.push(['uodId', '出现次数']);
            for (const d of vReport.duplicateIds) {
                vAoa.push([d.uodId, d.count]);
            }
            vAoa.push([]);
        }

        if (vReport.fieldMismatches.length > 0) {
            vAoa.push(['=== 字段错位明细 ===']);
            vAoa.push(['uodId', '字段', '原始值', '结果值']);
            for (const m of vReport.fieldMismatches) {
                vAoa.push([m.uodId, m.field, m.originalValue, m.resultValue]);
            }
        }

        const wsValidation = XLSX.utils.aoa_to_sheet(vAoa);
        wsValidation['!cols'] = [{ wch: 20 }, { wch: 20 }, { wch: 30 }, { wch: 30 }];
        XLSX.utils.book_append_sheet(wb, wsValidation, '数据校验');
    }

    // 生成文件名
    const now = new Date();
    const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
    const fileName = `拆票结果_${ts}.xlsx`;

    XLSX.writeFile(wb, fileName);
    showAlert(`已下载：${fileName}`, 'success');
}

// ========================
// UI辅助函数
// ========================
function showProcessing(show) {
    document.getElementById('processingOverlay').style.display = show ? 'flex' : 'none';
}

function updateProgress(msg) {
    document.getElementById('progressText').textContent = msg;
}

function showAlert(msg, type) {
    const alert = document.getElementById('alertBox');
    alert.textContent = msg;
    alert.className = 'alert ' + (type || 'info');
    alert.style.display = 'block';
    setTimeout(() => { alert.style.display = 'none'; }, 5000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================
// 显示规则配置
// ========================
function displayConfig() {
    const container = document.getElementById('configDisplay');

    const rules = [
        { label: '征税规则', content: '原产国为"中国"才拆征税，"中国台湾"不算（永芯/北京/北方/京城）' },
        { label: '行数限制', content: '永芯：一票最多40行，超出自动拆分' },
        { label: 'PO分组', content: '北京/北方/京城/永芯：每票最多5个PO；天津：每票最多3个PO' },
        { label: '高亮规则', content: '同票内同产品编号，大PO/小PO/备案单位/原产国任一不同→整行黄色+不同单元格红色' },
        { label: '131豁免', content: '原产国美国且HSCODE不在豁免清单→131豁免外；其他情况→非131豁免外' },
        { label: '分票编号', content: '免表票前缀MB，征税票前缀TAX，格式：前缀+月日+序号' }
    ];

    let html = '';
    for (const r of rules) {
        html += `<div class="config-item"><strong>${r.label}</strong>：${r.content}</div>`;
    }
    container.innerHTML = html;
}

// ========================
// 初始化
// ========================
document.addEventListener('DOMContentLoaded', () => {
    initFileUpload();
    displayConfig();

    // 下载按钮
    document.getElementById('downloadBtn').addEventListener('click', downloadResult);

    // 标签页切换
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(tab.dataset.tab).classList.add('active');
        });
    });
});
