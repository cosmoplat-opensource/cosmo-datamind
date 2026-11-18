"""可写入 XML 1.0 的文本判定，供各录入入口共用。"""
import re

# XML 1.0 不允许的控制字符、代理项与非字符；rdflib 写出后将无法再解析。
INVALID_XML_TEXT = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')


def invalid_xml_text(value):
    """非字符串或含不可写入 XML 的字符时返回 True。"""
    return not isinstance(value, str) or INVALID_XML_TEXT.search(value) is not None
