#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from dataclasses import dataclass
from typing import List
import const

@dataclass
class Signal:
    """Класс для описания сигнала"""
    name: str           # Имя сигнала (например, "TU.Open")
    mek_type: str       # Тип по МЭК-104 (например, "45")
    direction: str      # Направление: "output" или "input"

    def get_direction_code(self) -> str:
        """Возвращает код направления для драйвера"""
        return "\\5" if self.direction == "output" else "\\2"
    
    def get_driver_type(self) -> str:
        """Возвращает тип драйвера (datatype) в зависимости от типа МЭК-104"""
        type_map = {
            "30": "521",  # Тип 30 - Одноэлементная информация (ТС)
            "31": "521",  # Тип 31 - Одноэлементная информация с меткой времени (ТС)
            "36": "526",  # Тип 36 - Измерение (ТИ) - float
            "37": "526",  # Тип 37 - Измерение с меткой времени (ТИ)
            "45": "532",  # Тип 45 - Одноэлементное управление (ТУ)
            "46": "532",  # Тип 46 - Двойное управление (ТУ)
            "50": "526",  # Тип 50 - Уставка (ТР) - float
            "51": "526",  # Тип 51 - Уставка с меткой времени (ТР)
            "58": "532",  # Тип 58 - Управление шаговым переключателем (ТУ)
            "59": "532",  # Тип 59 - Управление шаговым переключателем с меткой времени
        }
        return type_map.get(self.mek_type, "526")  # По умолчанию 526 (ТИ)

# Глобальный список сигналов
SIGNALS: List[Signal] = [
    Signal("TU.ToOpen", "45", "output"),      
    Signal("TU.ToClose", "45", "output"),     
    Signal("TS.Opened", "30", "input"),     
    Signal("TS.Closed", "30", "input"),
    Signal("TI.Pin", "36", "input"),
    Signal("TI.Pout", "36", "input"),
    Signal("TR.SetTimeOpen", "50", "output"),
    Signal("TR.SetTimeClose", "50", "output")
]

def ioa_to_bytes(ioa):
    """
    Преобразует числовой IOA в формат трех октетов (байтов)
    Возвращает строку вида "X.Y.Z" где:
    X - старший байт (реально используется для расширения)
    Y - средний байт
    Z - младший байт
    """
    # Младший байт (первый октет в адресе)
    byte1 = ioa & 0xFF
    # Средний байт (второй октет)
    byte2 = (ioa >> 8) & 0xFF
    # Старший байт (третий октет) - обычно не используется для небольших адресов
    byte3 = (ioa >> 16) & 0xFF
    
    return f"{byte3}.{byte2}.{byte1}"

def generate_datapoint_section(type_name, name_template, start, end):
    """Генерирует секцию Datapoint/DpId"""
    lines = ["\n# Datapoint/DpId", "DpName\tTypeName\tID"]
    
    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        lines.append(f"{dp_name}\t{type_name}\t0")
    
    return "\n".join(lines)

def generate_distribution_section(type_name, name_template, start, end, num_drv):
    """Генерирует секцию DistributionInfo"""
    lines = ["\n# DistributionInfo", 
             "Manager/User\tElementName\tTypeName\t_distrib.._type\t_distrib.._driver"]
    
    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        for signal in SIGNALS:
            lines.append(f"ASC (1)/0\t{dp_name}.{signal.name}\t{type_name}\t56\t\\{num_drv}")
    
    return "\n".join(lines)

def generate_periphaddr_section(type_name, name_template, start, end, ca):
    """Генерирует секцию PeriphAddrMain"""
    lines = ["\n# PeriphAddrMain",
             "Manager/User\tElementName\tTypeName\t_address.._type\t_address.._reference\t_address.._poll_group\t_address.._connection\t_address.._offset\t_address.._subindex\t_address.._direction\t_address.._internal\t_address.._lowlevel\t_address.._active\t_address.._start\t_address.._interval\t_address.._reply\t_address.._datatype\t_address.._drv_ident"]
    
    zero_date = "01.01.1970 00:00:00.000"
    
    # Счетчик IOA (начинаем с 1 для первого сигнала)
    ioa_counter = 1
    
    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        
        for signal in SIGNALS:
            # Преобразуем IOA в формат трех октетов
            ioa_bytes = ioa_to_bytes(ioa_counter)
            
            # Формируем reference:
            # CLN2-{mek_type}.0.2.{ioa_bytes}
            # где:
            # 0.2 - фиксированные октеты CA (Common Address)
            # {ioa_bytes} - IOA в формате старший.средний.младший
            ref = f"\"CLN2-{signal.mek_type}.{ca}.{ioa_bytes}\""
            
            lines.append(f"ASC (1)/0\t{dp_name}.{signal.name}\t{type_name}\t16\t{ref}\t \t \t0\t0\t{signal.get_direction_code()}\t0\t0\t1\t{zero_date}\t{zero_date}\t{zero_date}\t{signal.get_driver_type()}\t\"IEC\"")
            
            # Увеличиваем счетчик IOA для следующего сигнала
            ioa_counter += 1
    
    return "\n".join(lines)

def generate_signals_csv(ca_num: int, name_template: str, start: int, end: int) -> str:
    """Генерирует содержимое signals.csv для симулятора (тот же порядок сигналов и IOA, что и в DPL)."""
    lines = ["id\tca\tioa\tasdu\tname\tdsc\tval\tthreshold"]
    sig_id = 1
    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        for s in SIGNALS:
            asdu = int(s.mek_type)
            name = f"{dp_name}.{s.name}"
            is_float = asdu in const.FLOAT_ASDU
            val = "0.0" if is_float else "0"
            thresh = "0" if asdu in const.COMMAND_ASDU else ("0.1" if is_float else "")
            lines.append(f"{sig_id}\t{ca_num}\t{sig_id}\t{asdu}\t{name}\t\t{val}\t{thresh}")
            sig_id += 1
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description='Генератор ascii dump файла для базы данных')
    parser.add_argument('--type', default='ZDV', help='Тип элементов (по умолчанию: ZDV)')
    parser.add_argument('--template', default='KP_1_ZDV_{}', help='Шаблон имени со счетчиком (по умолчанию: KP_1_ZDV_{})')
    parser.add_argument('--start', type=int, default=1, help='Начальное значение счетчика (по умолчанию: 1)')
    parser.add_argument('--end', '-e', type=int, required=True, help='Конечное значение счетчика')
    parser.add_argument('--output', '-o', default='kp_1.dpl', help='Выходной файл (по умолчанию: output.txt)')
    parser.add_argument('--ca', '-c', default='0.2', help='Номер КП(ca) для DPL reference')
    parser.add_argument('--ca-num', type=int, default=2, help='Номер КП (ca) для signals.csv')
    parser.add_argument('--drv', '-d', default='2', help='Номер драйвера по умолчанию')
    parser.add_argument('--signals-csv', '-s', default='signal.csv', help='Дополнительно записать signals.csv для симулятора')

    args = parser.parse_args()
    
    # Проверка корректности диапазона
    if args.start > args.end:
        print("Ошибка: начальное значение не может быть больше конечного")
        return
    
    # Заголовок файла
    header = "# ascii dump of database\n"
    
    # Генерируем все секции
    sections = [
        header,
        generate_datapoint_section(args.type, args.template, args.start, args.end),
        generate_distribution_section(args.type, args.template, args.start, args.end, args.drv),
        generate_periphaddr_section(args.type, args.template, args.start, args.end, args.ca)
    ]
    
    # Объединяем все секции
    content = "\n".join(sections)
    
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(content)

    if args.signals_csv:
        csv_content = generate_signals_csv(args.ca_num, args.template, args.start, args.end)
        with open(args.signals_csv, 'w', encoding='utf-8') as f:
            f.write(csv_content)
        print(f"Файл {args.signals_csv} (signals.csv) создан.")

    # Подсчет статистики
    signals_per_device = len(SIGNALS)
    total_signals = (args.end - args.start + 1) * signals_per_device
    last_ioa = total_signals
    
    print(f"Файл {args.output} успешно создан!")
    print(f"Сгенерировано устройств: {args.end - args.start + 1}")
    print(f"Всего сигналов: {total_signals}")
    print(f"Тип: {args.type}")
    print(f"Шаблон имени: {args.template}")
    print(f"Диапазон: {args.start} - {args.end}")
    print(f"CA (Common Address): 0.2 (фиксированный)")
    print(f"IOA: нарастающий от 1 до {last_ioa}")
    
    # Показываем информацию о сигналах
    print("\nСписок сигналов:")
    for idx, signal in enumerate(SIGNALS, 1):
        direction_str = "OUTPUT" if signal.direction == "output" else "INPUT "
        print(f"  {idx:2d}. {signal.name:15} {direction_str} (МЭК-{signal.mek_type} -> драйвер {signal.get_driver_type()})")
    
    # Показываем примеры первых IOA
    print("\nПримеры первых сигналов первого устройства:")
    ioa_counter = 1
    for signal in SIGNALS:
        ioa_bytes = ioa_to_bytes(ioa_counter)
        print(f"  IOA {ioa_counter:2d} ({signal.name:15}) -> CLN2-{signal.mek_type}.0.2.{ioa_bytes} [{signal.get_direction_code()}]")
        ioa_counter += 1

if __name__ == "__main__":
    main()