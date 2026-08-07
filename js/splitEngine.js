/**
 * 拆票引擎 - 核心逻辑
 * 从VBA代码完整移植，适配Web环境
 */

class SplitEngine {
    constructor(config) {
        this.config = config;
    }

    /**
     * 主处理入口
     * @param {Array<Object>} rows - 数据行（每行为一个对象，键为列名）
     * @param {Set<string>} exemptionCodes - 豁免清单（8位HS编码）
     * @returns {Object} 处理结果 { resultRows, inconsistentInfo, reasons }
     */
    process(rows, exemptionCodes) {
        if (!rows || rows.length === 0) {
            return { resultRows: [], inconsistentInfo: [], reasons: [] };
        }

        // 第一阶段：构建基础分票标记
        const { baseMarks, poNums, markInfo } = this.buildBaseMarks(rows, exemptionCodes);

        // 第二阶段：PO分组
        const finalMarkDict = this.groupByPOWithFallback(baseMarks, poNums);

        // 第三阶段：行级映射
        let rowFinalMarks = this.mapRowsToFinalMark(baseMarks, poNums, finalMarkDict);

        // 第三阶段补丁：追加进库备注
        rowFinalMarks = this.appendInboundRemarks(rows, rowFinalMarks);

        // 新增阶段：永芯行数限制（一票最多40行）
        rowFinalMarks = this.applyRowLimitForYongxin(rowFinalMarks);

        // 第四阶段：生成分票编号
        const ticketNumbers = this.generateTicketNumbers(rowFinalMarks);

        // 回写结果
        const resultRows = this.buildResultRows(rows, rowFinalMarks, ticketNumbers);

        // 高亮检查：同票内不一致行
        const inconsistentInfo = this.findInconsistentRows(resultRows);

        // 排序 + 插入空行
        const sortedRows = this.sortByTicketNumber(resultRows);

        // 生成拆分理由
        const reasons = this.generateSplitReasons(finalMarkDict, markInfo, ticketNumbers);

        return {
            resultRows: sortedRows,
            inconsistentInfo,
            reasons,
            ticketNumbers
        };
    }

    // ========================
    // HSCODE清洗
    // ========================
    cleanHSCode(hsCode) {
        if (!hsCode) hsCode = '';
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
    // 获取列值
    // ========================
    getCellValue(row, columnName) {
        if (row[columnName] != null) return String(row[columnName]).trim();
        // 尝试模糊匹配
        for (const key of Object.keys(row)) {
            if (key && key.includes(columnName)) {
                const val = row[key];
                return val != null ? String(val).trim() : '';
            }
        }
        return '';
    }

    // ========================
    // 构建基础分票标记
    // ========================
    buildBaseMarks(rows, exemptionCodes) {
        const baseMarks = [];
        const poNums = [];
        const markInfo = {};

        for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            const result = this.buildSingleBaseMark(row, exemptionCodes);
            baseMarks.push(result.mark);
            poNums.push(result.poNum);

            if (!markInfo[result.mark]) {
                markInfo[result.mark] = result.info;
            }
        }

        return { baseMarks, poNums, markInfo };
    }

    // ========================
    // 构建单行基础分票标记
    // ========================
    buildSingleBaseMark(row, exemptionCodes) {
        let mark = '';
        const info = {};

        // 账册号后三位
        const accountNum = this.getCellValue(row, '账册号');
        const accountSuffix = accountNum.length >= 3
            ? accountNum.slice(-3)
            : accountNum;
        mark = accountSuffix;
        info.AccountSuffix = accountSuffix;

        // 业务申报表号
        const bizAppNum = this.getCellValue(row, '业务申报表号');
        if (!bizAppNum) {
            mark += '_无业务申报表号';
        } else {
            mark += '_' + bizAppNum;
        }
        info.BizAppNum = bizAppNum || '无业务申报表号';

        // 客户类型
        const customerOrder = this.getCellValue(row, '客户指令号');
        let custType = '';
        let isTianjin = false;
        let isYongxin = false;

        for (const ct of this.config.customerTypes) {
            if (customerOrder.includes(ct.pattern)) {
                mark += '_' + ct.label;
                custType = ct.label;
                isTianjin = ct.isTianjin || false;
                isYongxin = ct.isYongxin || false;
                break;
            }
        }

        if (!custType) {
            mark += '_未知客户_' + customerOrder.substring(0, 10);
            custType = '未知客户';
        }
        info.CustType = custType;
        info.IsTianjin = isTianjin;
        info.IsYongxin = isYongxin;

        // 征税/免表
        const originCountry = this.getCellValue(row, '原产国');
        const productName = this.getCellValue(row, '产品名称');

        let taxResult;
        if (isTianjin) {
            taxResult = this.applyTianjinTaxRule(mark, originCountry, productName);
        } else {
            taxResult = this.applyBeijingTaxRule(mark, originCountry, productName);
        }
        mark = taxResult.mark;
        info.TaxType = taxResult.taxType;
        info.TaxReason = taxResult.taxReason;

        // 131豁免
        const hsCode = this.cleanHSCode(this.getCellValue(row, 'HSCODE'));
        let exemptStatus = '';
        let exemptReason = '';

        if (originCountry.includes('美国')) {
            if (hsCode && exemptionCodes.has(hsCode)) {
                mark += '_非131豁免外';
                exemptStatus = '非131豁免外';
                exemptReason = '原产国为美国，但HSCODE在豁免清单中';
            } else {
                mark += '_131豁免外';
                exemptStatus = '131豁免外';
                exemptReason = '原产国为美国，且HSCODE不在豁免清单中';
            }
        } else {
            mark += '_非131豁免外';
            exemptStatus = '非131豁免外';
            exemptReason = '原产国非美国';
        }
        info.ExemptStatus = exemptStatus;
        info.ExemptReason = exemptReason;

        // 有效PO号（优先大PO，其次小PO）
        const bigPO = this.getCellValue(row, '大PO');
        const smallPO = this.getCellValue(row, '小PO');
        const effectivePONum = bigPO || smallPO || '';

        return { mark, poNum: effectivePONum, info };
    }

    // ========================
    // 北京征税免表规则（更新版：中国台湾不算中国）
    // ========================
    applyBeijingTaxRule(mark, originCountry, productName) {
        const ruleConfig = this.config.beijingTaxRule;

        for (const rule of ruleConfig.rules) {
            const m = rule.match;

            // 检查原产国匹配
            if (m.originCountry) {
                if (!originCountry.includes(m.originCountry)) continue;
                // 排除条件（如排除"台湾"）
                if (m.originCountryExclude && originCountry.includes(m.originCountryExclude)) continue;
                return {
                    mark: mark + '_' + rule.type,
                    taxType: rule.type,
                    taxReason: rule.reason
                };
            }

            // 检查产品名称匹配
            if (m.productName) {
                let matched = false;
                for (const pn of m.productName) {
                    if (productName.includes(pn)) { matched = true; break; }
                }
                if (matched) {
                    return {
                        mark: mark + '_' + rule.type,
                        taxType: rule.type,
                        taxReason: rule.reason
                    };
                }
            }
        }

        return {
            mark: mark + '_' + ruleConfig.default.type,
            taxType: ruleConfig.default.type,
            taxReason: ruleConfig.default.reason
        };
    }

    // ========================
    // 天津征税免表规则（未变更）
    // ========================
    applyTianjinTaxRule(mark, originCountry, productName) {
        const ruleConfig = this.config.tianjinTaxRule;

        for (const rule of ruleConfig.rules) {
            const m = rule.match;

            if (m.originCountry) {
                if (originCountry.includes(m.originCountry)) {
                    return {
                        mark: mark + '_' + rule.type,
                        taxType: rule.type,
                        taxReason: rule.reason
                    };
                }
            }

            if (m.productName) {
                for (const pn of m.productName) {
                    if (productName.includes(pn)) {
                        return {
                            mark: mark + '_' + rule.type,
                            taxType: rule.type,
                            taxReason: rule.reason
                        };
                    }
                }
            }
        }

        return {
            mark: mark + '_' + ruleConfig.default.type,
            taxType: ruleConfig.default.type,
            taxReason: ruleConfig.default.reason
        };
    }

    // ========================
    // PO分组（含兜底逻辑）
    // ========================
    groupByPOWithFallback(baseMarks, poNums) {
        // 按baseMark收集PO
        const baseMarkPOs = {};
        for (let i = 0; i < baseMarks.length; i++) {
            const bm = baseMarks[i];
            const po = poNums[i];
            if (!baseMarkPOs[bm]) baseMarkPOs[bm] = [];
            if (po && !baseMarkPOs[bm].includes(po)) {
                baseMarkPOs[bm].push(po);
            }
        }

        const finalMarkDict = {};

        for (const bm of Object.keys(baseMarkPOs)) {
            // 确定PO分组上限
            const maxPO = bm.includes('天津')
                ? this.config.maxPOPerTicket['天津中芯']
                : this.config.maxPOPerTicket['default'];

            const poList = baseMarkPOs[bm];

            if (poList.length === 0) {
                finalMarkDict[bm + '_无PO'] = '无';
                continue;
            }

            // 分离混合PO和普通PO
            const mixedPOs = poList.filter(po => this.isMixedAlphaNumeric(po));
            const normalPOs = poList.filter(po => !this.isMixedAlphaNumeric(po));

            // 普通PO分组
            if (normalPOs.length > 0) {
                const groups = this.groupNormalPO(normalPOs, maxPO);
                for (const gKey of Object.keys(groups)) {
                    const fMark = bm + '_' + gKey;
                    finalMarkDict[fMark] = groups[gKey].join(',');
                }
            }

            // 混合PO各自单独分组
            for (const mpo of mixedPOs) {
                finalMarkDict[bm + '_混合PO_' + mpo] = mpo;
            }
        }

        return finalMarkDict;
    }

    // ========================
    // 判断是否为数字混合字母的PO
    // ========================
    isMixedAlphaNumeric(poNumber) {
        if (!poNumber) return false;
        let hasDigit = false, hasLetter = false;
        for (let i = 0; i < poNumber.length; i++) {
            const c = poNumber.charCodeAt(i);
            if (c >= 48 && c <= 57) hasDigit = true;        // 0-9
            if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) hasLetter = true; // A-Za-z
            if (hasDigit && hasLetter) return true;
        }
        return false;
    }

    // ========================
    // 普通PO分组
    // ========================
    groupNormalPO(poList, maxPO) {
        const groups = {};
        let idx = 0;
        let groupIndex = 1;

        while (idx < poList.length) {
            const groupKey = 'PO组' + String(groupIndex).padStart(2, '0');
            groups[groupKey] = [];

            let cnt = 0;
            while (cnt < maxPO && idx < poList.length) {
                groups[groupKey].push(poList[idx]);
                cnt++;
                idx++;
            }
            groupIndex++;
        }

        return groups;
    }

    // ========================
    // 行级映射
    // ========================
    mapRowsToFinalMark(baseMarks, poNums, finalMarkDict) {
        const rowFinalMarks = [];
        const fmKeys = Object.keys(finalMarkDict);

        for (let i = 0; i < baseMarks.length; i++) {
            const baseMark = baseMarks[i];
            const poNum = poNums[i];
            let matched = false;

            for (const fm of fmKeys) {
                if (fm.includes(baseMark)) {
                    const poList = finalMarkDict[fm];
                    const poMatch = (!poNum || poList === '无' || poList.includes(poNum));
                    if (poMatch) {
                        rowFinalMarks.push(fm);
                        matched = true;
                        break;
                    }
                }
            }

            if (!matched) {
                rowFinalMarks.push(baseMark + '_未分组');
            }
        }

        return rowFinalMarks;
    }

    // ========================
    // 追加进库备注
    // ========================
    appendInboundRemarks(rows, rowFinalMarks) {
        const remarkCol = this.config.inboundRemarkColumn;
        const newMarks = [];

        for (let i = 0; i < rows.length; i++) {
            let remark = this.getCellValue(rows[i], remarkCol);
            remark = remark.replace(/_/g, '-'); // 防止破坏下划线分隔结构

            const currentMark = rowFinalMarks[i];
            if (!remark) {
                newMarks.push(currentMark + '_不含组件');
            } else {
                newMarks.push(currentMark + '_' + remark);
            }
        }

        return newMarks;
    }

    // ========================
    // 永芯行数限制（一票最多40行）
    // ========================
    applyRowLimitForYongxin(rowFinalMarks) {
        const maxRows = this.config.maxRowsPerTicket['永芯'] || 40;

        // 统计每个mark的行数和行索引
        const markRows = {};
        for (let i = 0; i < rowFinalMarks.length; i++) {
            const mk = rowFinalMarks[i];
            if (!markRows[mk]) markRows[mk] = [];
            markRows[mk].push(i);
        }

        const newMarks = [...rowFinalMarks];

        for (const mk of Object.keys(markRows)) {
            // 只对永芯分组且超过限制行数的进行拆分
            if (mk.includes('永芯') && markRows[mk].length > maxRows) {
                const rows = markRows[mk];
                let cnt = 0;
                let currentSub = 1;

                for (const rowIdx of rows) {
                    if (cnt >= maxRows) {
                        currentSub++;
                        cnt = 0;
                    }
                    const subMark = mk + '_拆分' + String(currentSub).padStart(2, '0');
                    newMarks[rowIdx] = subMark;
                    cnt++;
                }
            }
        }

        return newMarks;
    }

    // ========================
    // 生成分票编号
    // ========================
    generateTicketNumbers(rowFinalMarks) {
        const dict = {};
        const dateStr = this.formatDate();

        let seq = 1;
        for (const finalMark of rowFinalMarks) {
            if (!dict[finalMark]) {
                const prefix = finalMark.includes('_免表')
                    ? this.config.ticketPrefix['免表']
                    : this.config.ticketPrefix['default'];
                dict[finalMark] = prefix + dateStr + String(seq).padStart(2, '0');
                seq++;
            }
        }

        return dict;
    }

    // ========================
    // 格式化日期为mmdd
    // ========================
    formatDate() {
        const now = new Date();
        const mm = String(now.getMonth() + 1).padStart(2, '0');
        const dd = String(now.getDate()).padStart(2, '0');
        return mm + dd;
    }

    // ========================
    // 构建结果行
    // ========================
    buildResultRows(rows, rowFinalMarks, ticketNumbers) {
        const result = [];

        for (let i = 0; i < rows.length; i++) {
            const finalMark = rowFinalMarks[i];
            const ticketNo = ticketNumbers[finalMark] || 'ERROR';

            const resultRow = {
                ...rows[i],
                '分票编号': ticketNo,
                '分票': finalMark
            };
            result.push(resultRow);
        }

        return result;
    }

    // ========================
    // 查找不一致行（同票同产品编号，维度任一不同）
    // 直接将高亮信息绑定到行对象上，避免排序后索引错位
    // ========================
    findInconsistentRows(resultRows) {
        const dims = this.config.highlightDimensions;
        const groups = {};

        // 按 (ticketNo, productCode) 分组
        for (let i = 0; i < resultRows.length; i++) {
            const row = resultRows[i];
            const ticketNo = String(row['分票编号'] || '');
            const productCode = this.getCellValue(row, '产品编号');
            if (!productCode) continue;

            const key = ticketNo + '|' + productCode;
            if (!groups[key]) {
                groups[key] = {
                    rows: [],
                    values: dims.reduce((acc, d) => { acc[d.columnName] = new Set(); return acc; }, {})
                };
            }

            groups[key].rows.push(row); // 存行对象引用而非索引
            for (const d of dims) {
                const val = this.getCellValue(row, d.columnName);
                if (val) groups[key].values[d.columnName].add(val);
            }
        }

        // 找出有不一致的组，直接标记行对象
        let highlightedCount = 0;

        for (const key of Object.keys(groups)) {
            const group = groups[key];
            const inconsistentDims = [];

            for (const d of dims) {
                if (group.values[d.columnName].size > 1) {
                    inconsistentDims.push(d.columnName);
                }
            }

            if (inconsistentDims.length > 0) {
                for (const row of group.rows) {
                    row._isInconsistent = true;
                    row._redColumns = new Set(inconsistentDims);
                    highlightedCount++;
                }
            }
        }

        return { highlightedRows: { size: highlightedCount } };
    }

    // ========================
    // 按分票编号排序并插入空行
    // ========================
    sortByTicketNumber(resultRows) {
        // 按分票编号排序
        const sorted = [...resultRows].sort((a, b) => {
            const aNo = String(a['分票编号'] || '');
            const bNo = String(b['分票编号'] || '');
            return aNo.localeCompare(bNo);
        });

        // 在不同分票编号之间插入空行标记
        const result = [];
        for (let i = 0; i < sorted.length; i++) {
            if (i > 0) {
                const prevNo = String(sorted[i - 1]['分票编号'] || '');
                const currNo = String(sorted[i]['分票编号'] || '');
                if (prevNo !== currNo) {
                    result.push(null); // 空行标记
                }
            }
            result.push(sorted[i]);
        }

        return result;
    }

    // ========================
    // 生成拆分理由
    // ========================
    generateSplitReasons(finalMarkDict, markInfo, ticketNumbers) {
        const reasons = [];
        let rowNum = 1;

        for (const key of Object.keys(finalMarkDict)) {
            const ticketNo = ticketNumbers[key] || '';

            // 查找对应的baseMark信息
            let info = null;
            let baseMark = '';
            for (const mk of Object.keys(markInfo)) {
                if (key.includes(mk)) {
                    baseMark = mk;
                    info = markInfo[mk];
                    break;
                }
            }

            if (!info) {
                info = {
                    AccountSuffix: '未知', BizAppNum: '未知', CustType: '未识别',
                    TaxType: '未知', TaxReason: '无', ExemptStatus: '未知', ExemptReason: '无'
                };
            }

            const poList = finalMarkDict[key] || '无';

            let reason = `1. 账册号后三位：${info.AccountSuffix}；\n`;
            reason += `2. 业务申报表号：${info.BizAppNum}；\n`;
            reason += `3. 客户类型：${info.CustType}；\n`;
            reason += `4. 征税/免表：${info.TaxType}（原因：${info.TaxReason}）；\n`;
            reason += `5. 131豁免状态：${info.ExemptStatus}（原因：${info.ExemptReason}）；\n`;
            reason += `6. PO号（优先使用大PO）：${poList}；\n`;
            reason += `7. 进库备注：已在行级最终标记中追加。`;

            if (key.includes('永芯')) {
                reason += `\n8. 永芯规则：一票最多40行，超出已自动拆分。`;
            }

            if (key.includes('_混合PO_')) {
                reason += `\n注意：此组包含数字混合字母PO（${poList}），已单独分组。`;
            }

            reasons.push({
                ticketNo,
                mark: key,
                reason,
                rowNum: rowNum++
            });
        }

        return reasons;
    }

    // ========================
    // uodId数据完整性校验
    // 对比原始数据和拆票结果，检测行丢失/多余/重复/字段错位
    // ========================
    validateByUodId(originalRows, resultRows) {
        const idColumn = this.config.uodIdColumn || 'uodId';
        const report = {
            passed: true,
            originalCount: 0,
            resultCount: 0,
            countMatch: true,
            missingIds: [],      // 原始有但结果没有的uodId
            extraIds: [],        // 结果有但原始没有的uodId
            duplicateIds: [],    // 结果中重复的uodId
            fieldMismatches: [], // 同uodId但字段值不一致（数据错位）
            totalMismatches: 0
        };

        // 构建原始数据映射: uodId -> row
        const originalMap = {};
        const originalIds = [];
        for (const row of originalRows) {
            const uid = String(this.getCellValue(row, idColumn)).trim();
            if (!uid || uid === 'undefined') continue;
            originalMap[uid] = row;
            originalIds.push(uid);
        }
        report.originalCount = originalIds.length;

        // 构建结果数据映射: uodId -> row（跳过空行分隔符）
        const resultMap = {};
        const resultIds = [];
        for (const row of resultRows) {
            if (row === null) continue;
            const uid = String(this.getCellValue(row, idColumn)).trim();
            if (!uid || uid === 'undefined') continue;
            resultIds.push(uid);
            if (!resultMap[uid]) {
                resultMap[uid] = row;
            }
        }
        report.resultCount = resultIds.length;

        // 检查1: 行数匹配
        report.countMatch = (originalIds.length === resultIds.length);
        if (!report.countMatch) {
            report.passed = false;
        }

        // 检查2: 丢失的uodId（原始有，结果没有）
        for (const id of originalIds) {
            if (!resultMap[id]) {
                report.missingIds.push(id);
            }
        }
        if (report.missingIds.length > 0) report.passed = false;

        // 检查3: 多余的uodId（结果有，原始没有）
        for (const id of resultIds) {
            if (!originalMap[id]) {
                report.extraIds.push(id);
            }
        }
        if (report.extraIds.length > 0) report.passed = false;

        // 检查4: 重复的uodId
        const idCounts = {};
        for (const id of resultIds) {
            idCounts[id] = (idCounts[id] || 0) + 1;
        }
        for (const id of Object.keys(idCounts)) {
            if (idCounts[id] > 1) {
                report.duplicateIds.push({ uodId: id, count: idCounts[id] });
            }
        }
        if (report.duplicateIds.length > 0) report.passed = false;

        // 检查5: 字段值一致性（检测数据错位）
        // 对每个结果行，找原始数据中同uodId的行，逐字段比对
        const skipKeys = new Set(['分票编号', '分票', '_isInconsistent', '_redColumns']);
        const checkedRows = new Set(); // 避免重复检查

        for (const row of resultRows) {
            if (row === null) continue;
            const uid = String(this.getCellValue(row, idColumn)).trim();
            if (!uid || checkedRows.has(uid)) continue;
            checkedRows.add(uid);

            const origRow = originalMap[uid];
            if (!origRow) continue;

            // 逐字段比对原始数据的所有列
            for (const key of Object.keys(origRow)) {
                if (skipKeys.has(key)) continue;
                const origVal = origRow[key] != null ? String(origRow[key]).trim() : '';
                const resultVal = row[key] != null ? String(row[key]).trim() : '';

                if (origVal !== resultVal) {
                    report.fieldMismatches.push({
                        uodId: uid,
                        field: key,
                        originalValue: origVal,
                        resultValue: resultVal,
                        originalRow: origRow,
                        resultRow: row
                    });
                }
            }
        }

        if (report.fieldMismatches.length > 0) report.passed = false;
        report.totalMismatches = report.missingIds.length + report.extraIds.length
            + report.duplicateIds.length + report.fieldMismatches.length;

        return report;
    }
}
